from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "kit_deployment.py"
KIT_RELATIVE = Path(".basicly") / "core" / "kit" / "tracker"
LEDGER_RELATIVE = Path(".basicly") / "ledger"

LOG_RULE = "events-*.jsonl -text merge=union"
SNAPSHOT_RULE = ".basicly/ledger/snapshot.jsonl"
CHECKPOINT_RULE = ".basicly/ledger/checkpoint-*.jsonl"

CLOCK = 1_000_000_000.0


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "kit_deployment")
snapshot = _load(REPO_ROOT / KIT_RELATIVE / "snapshot.py", "kit_deployment_test_snapshot")
events = snapshot.events


def git_env(tmp_path: Path) -> dict[str, str]:

    absent = str(tmp_path / "no-such-gitconfig")
    return {**os.environ, "GIT_CONFIG_GLOBAL": absent, "GIT_CONFIG_SYSTEM": absent}


def git(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )


def init(root: Path, env: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(root)], check=True, capture_output=True, env=env
    )
    git(root, env, "config", "user.email", "test@example.invalid")
    git(root, env, "config", "user.name", "kit deployment test")
    git(root, env, "config", "commit.gpgsign", "false")
    git(root, env, "config", "core.autocrlf", "false")


def drop_lines(path: Path, *lines: str) -> None:

    wanted = {line.strip() for line in lines}
    kept = []
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() in wanted:
            seen.add(line.strip())
        else:
            kept.append(line)
    missing = sorted(wanted - seen)
    assert not missing, f"{path.name} does not carry {missing} — nothing to remove"
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def make_host(root: Path, env: dict[str, str]) -> Path:

    shutil.copytree(REPO_ROOT / KIT_RELATIVE, root / KIT_RELATIVE)
    for name in (".gitattributes", ".gitignore"):
        shutil.copy2(REPO_ROOT / name, root / name)
    init(root, env)
    return root


def write_ledger(directory: Path) -> None:

    events.append(
        directory,
        [events.Draft("basicly-aa11", events.KIND_CREATED, {"title": "a record"})],
        actor="test",
        clock=lambda: CLOCK,
    )
    snapshot.rotate(directory, "2026")
    events.append(
        directory,
        [events.Draft("basicly-bb22", events.KIND_CREATED, {"title": "another record"})],
        actor="test",
        clock=lambda: CLOCK,
    )
    snapshot.rebuild(directory)


def run_gate(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
