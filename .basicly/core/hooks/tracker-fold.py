from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess  # nosec B404
import sys
import time
import tomllib
from pathlib import Path
from typing import Any


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 B607
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", check=False
    )


def main() -> int:
    root = Path.cwd()
    source = Path(__file__).resolve().parent.parent / "kit/tracker/locate.py"
    spec = importlib.util.spec_from_file_location("basicly_tracker_kit_locate", source)
    if spec is None or spec.loader is None:
        print("tracker-fold: the tracker kit is missing; reinstall basicly", file=sys.stderr)
        return 1
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        if module.harness().harness_ledger(root, time.time) is not None:
            return 0
    except (ValueError, OSError, ImportError) as exc:
        print(f"tracker-fold: {exc}", file=sys.stderr)
        return 1
    return _legacy(root)


def _legacy(root: Path) -> int:
    config = root / "basicly.toml"
    if not config.is_file():
        return 0
    settings = tomllib.loads(config.read_text(encoding="utf-8"))
    if not settings.get("tracker", {}).get("fold_on_merge", False):
        return 0
    default = git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    branch = default.stdout.strip().removeprefix("origin/")
    branch = branch or git(root, "config", "init.defaultBranch").stdout.strip() or "main"
    current = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if current.stdout.strip() != branch:
        return 0
    pending = git(root, "rev-list", "--count", "@{u}..HEAD")
    if pending.returncode == 0 and pending.stdout.strip() == "0":
        return 0
    status = git(root, "status", "--porcelain", "--", ".", ":(exclude).basicly/ledger")
    if (
        status.returncode
        or status.stdout.strip()
        or not any((root / ".basicly/ledger").glob("pending-*.jsonl"))
    ):
        return 0
    engine = shutil.which("basicly")
    command = (
        ["basicly", "tracker", "fold"] if engine else json.loads(os.environ["BASICLY_FOLD_COMMAND"])
    )
    print(
        f"tracker: pending shards are unchanged; run {shlex.join(command)} outside Git hooks",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
