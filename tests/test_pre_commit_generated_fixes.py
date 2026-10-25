from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import hooks, lefthook_config
from basicly.catalog import bundled_catalog_root
from tests import test_lefthook_migration
from tests.test_lefthook_migration import invoke, repository

isolated_uv = test_lefthook_migration.isolated_uv


@pytest.mark.parametrize("working_edit", [None, "working-only"])
@pytest.mark.parametrize("check_passes", [True, False])
def test_generated_auto_fixes_publish_only_validated_index_bytes(
    tmp_path: Path, working_edit: str | None, check_passes: bool
) -> None:
    assert os.environ["UV_CACHE_DIR"] == str(tmp_path / "uv-cache")
    repo = repository(tmp_path)
    source = bundled_catalog_root() / "hooks"
    for name in ("pre-commit.py", "check_runner.py", "command_paths.py"):
        shutil.copyfile(source / name, repo / ".basicly/core/hooks" / name)
    fixer = 'from pathlib import Path; Path("generated.txt").write_text("fresh")'
    check = 'from pathlib import Path; assert Path("generated.txt").read_text()=="fresh";'
    check += f" raise SystemExit({0 if check_passes else 3})"
    (repo / "basicly.toml").write_text(
        '[[verify.checks]]\nname="generated"\nmodes=["fast"]\n'
        + "fix_command="
        + json.dumps([sys.executable, "-c", fixer])
        + "\n"
        + "command="
        + json.dumps([sys.executable, "-c", check])
        + "\n"
    )
    lefthook_config.sync_config(
        repo,
        [hooks.HookSpec("verify", "pre-commit.py", "pre-commit", always_run=True)],
        ".basicly/core/hooks",
    )
    generated = repo / "generated.txt"
    generated.write_text("stale")
    changed = repo / "input.txt"
    changed.write_text("before")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    changed.write_text("after")
    subprocess.run(["git", "add", "input.txt"], cwd=repo, check=True)
    if working_edit is not None:
        generated.write_text(working_edit)
    result = invoke(repo, "git", "pre-commit")
    assert (result.returncode == 0) == check_passes, result.stdout + result.stderr
    staged = subprocess.run(
        ["git", "show", ":generated.txt"], cwd=repo, text=True, capture_output=True, check=True
    ).stdout
    assert staged == ("fresh" if check_passes else "stale")
    assert generated.read_text() == (working_edit if working_edit is not None else staged)
    assert changed.read_text() == "after"
