from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from basicly import lefthook_binary
from basicly.catalog import bundled_catalog_root


def repository(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid")):
        subprocess.run(["git", "config", key, value], cwd=path, check=True)
    destination = path / ".basicly/core/hooks"
    destination.mkdir(parents=True)
    for name in ("lefthook-runtime.py", "staged-snapshot.py"):
        shutil.copyfile(bundled_catalog_root() / "hooks" / name, destination / name)
    lefthook_binary.sync_binary(path)
    return path


def invoke(
    repo: Path, manager: str, stage: str, payload: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / ".basicly/core/hooks/lefthook-runtime.py"), manager, stage],
        cwd=repo,
        input=json.dumps(payload or {}),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
