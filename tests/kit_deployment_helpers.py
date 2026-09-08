"""Helpers shared by the tracker kit's deployment tests.

A host repository shaped the way a consumer's is — the kit copied in, this repo's own rule
files beside it — plus the hermetic git environment every call runs under and the gate run
as a subprocess. Plain functions, per this repo's helper-module pattern; each test module
declares its own ``env`` and ``host`` fixtures over them. ``test_kit_deployment.py``
asserts the ``-text`` rule and the ignore rules; ``test_kit_deployment_union_merge.py``
asserts the ``merge=union`` rule. Both build their hosts here, so a rule deleted from the
real ``.gitattributes`` breaks every negative control at the fixture, loudly, rather than
leaving one module asserting nothing.

The gate is loaded by path, the way a consumer without basicly would load it, and the kit's
``snapshot.py`` the same way; ``snapshot`` pulls ``events`` in itself.
"""

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

# The one line this repo's `.gitattributes` carries for the log, exactly as written there:
# `_drop_lines` removes it whole, so a spelling drift here fails the fixture.
LOG_RULE = "events-*.jsonl -text merge=union"
SNAPSHOT_RULE = ".basicly/ledger/snapshot.jsonl"
CHECKPOINT_RULE = ".basicly/ledger/checkpoint-*.jsonl"

# Injected rather than read, per this repo's platform-hermetic rule: the kit's only wall
# clock is this argument, and a ledger written from the host's clock is a different file
# on every run.
CLOCK = 1_000_000_000.0


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way a consumer without basicly would."""
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
    """An environment where git reads no global or system config.

    A developer's ``core.autocrlf`` or ``core.excludesFile`` would otherwise be an input:
    a global exclude file could make a negative control pass, which is the direction that
    turns a broken gate green. Both variables point at a file that is never created —
    git tolerates a missing config path, and this is portable in a way ``os.devnull`` is
    not.
    """
    absent = str(tmp_path / "no-such-gitconfig")
    return {**os.environ, "GIT_CONFIG_GLOBAL": absent, "GIT_CONFIG_SYSTEM": absent}


def git(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in ``repo``, raising on failure so a broken fixture is never a silent pass."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )


def init(root: Path, env: dict[str, str]) -> None:
    """Make ``root`` a git repository able to commit without touching the host's identity."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(root)], check=True, capture_output=True, env=env
    )
    git(root, env, "config", "user.email", "test@example.invalid")
    git(root, env, "config", "user.name", "kit deployment test")
    git(root, env, "config", "commit.gpgsign", "false")
    git(root, env, "config", "core.autocrlf", "false")


def drop_lines(path: Path, *lines: str) -> None:
    """Remove exact lines from a rules file, failing when one is not there to remove.

    The failure is the point: this is how a deleted rule in the real ``.gitattributes`` or
    ``.gitignore`` breaks the negative controls loudly instead of leaving them asserting
    nothing about a host that never had the rule.
    """
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
    """A git repository with the kit installed and this repo's own rule files.

    The kit is copied rather than referenced: ``basicly install`` puts it inside the repo
    it manages, so a host that reached back into this checkout would be an arrangement no
    consumer has.
    """
    shutil.copytree(REPO_ROOT / KIT_RELATIVE, root / KIT_RELATIVE)
    for name in (".gitattributes", ".gitignore"):
        shutil.copy2(REPO_ROOT / name, root / name)
    init(root, env)
    return root


def write_ledger(directory: Path) -> None:
    """Write a real ledger: two logs across a rotation, a checkpoint, and a snapshot.

    Driven through the kit's own API rather than by writing files with the expected names,
    so the derived set under test is whatever the kit really produces.
    """
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
    """Run the gate against ``repo``, never raising: a non-zero exit is the answer."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
