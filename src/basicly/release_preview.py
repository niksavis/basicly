from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from . import checkout

CONFIG_KEYS = (
    "basicly.holder",
    "user.name",
    "user.email",
    "user.signingkey",
    "commit.gpgsign",
    "gpg.format",
    "gpg.program",
    "core.autocrlf",
    "core.eol",
    "core.filemode",
)
SHARED_DIRECTORIES = (".venv", "node_modules", "build/lefthook")


def _prepare(root: Path, scratch: Path) -> None:
    checkout.git(["clone", "--shared", "--quiet", "--", str(root), str(scratch)])
    for key in CONFIG_KEYS:
        value = checkout.git(["config", "--get", key], cwd=root, check=False)
        if value.returncode == 0:
            checkout.git(["config", key, value.stdout.strip()], cwd=scratch)
    hooks = Path(checkout.git(["rev-parse", "--git-path", "hooks"], cwd=root).stdout.strip())
    hooks = hooks if hooks.is_absolute() else root / hooks
    if hooks.is_dir():
        shutil.copytree(hooks, scratch / ".git/hooks", dirs_exist_ok=True)
    checkout.git(["config", "core.hooksPath", ".git/hooks"], cwd=scratch)
    for name in (*SHARED_DIRECTORIES, ".basicly/bin"):
        source, target = root / name, scratch / name
        if not source.is_dir() or target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if name in SHARED_DIRECTORIES:
            try:
                target.symlink_to(source, target_is_directory=True)
                continue
            except OSError:
                pass
        shutil.copytree(source, target)
    local = root / "basicly.local.toml"
    if local.is_file() and not (scratch / local.name).exists():
        shutil.copyfile(local, scratch / local.name)


def run(root: Path, assemble: Callable[[Path], None], message: str) -> tuple[str, ...]:
    with tempfile.TemporaryDirectory(prefix="basicly-release-preview-") as directory:
        scratch = Path(directory) / "repo"
        _prepare(root.resolve(), scratch)
        assemble(scratch)
        checkout.git(["add", "-A"], cwd=scratch)
        env = {**os.environ, "PYTHONPATH": str(scratch / "src")}
        done = checkout.run(["git", "commit", "-m", message], cwd=scratch, env=env)
        output = (done.stdout or "") + (done.stderr or "")
        counts = tuple(line for line in output.splitlines() if line.startswith("checks passed:"))
        return counts or ("release preview: commit hooks passed; no verify-runner count reported",)
