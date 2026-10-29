from __future__ import annotations

import json
import os
import subprocess  # nosec B404
from datetime import UTC, datetime
from pathlib import Path

STORE = Path(".basicly/usage/check-passes.json")
KEEP = 20


def _git(root: Path, *args: str) -> str | None:
    done = subprocess.run(  # nosec B603 B607
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", check=False
    )
    return done.stdout if done.returncode == 0 else None


def key(root: Path, interpreter: str | None) -> str | None:
    if os.environ.get("CI") or not os.environ.get("BASICLY_SNAPSHOT_TREE") or not interpreter:
        return None
    listed = _git(root, "ls-files", "--others", "--exclude-standard")
    tree = _git(root, "write-tree")
    linked = set(os.environ.get("BASICLY_SNAPSHOT_LINKS", "").splitlines())
    if listed is None or set(listed.splitlines()) - linked or not tree:
        return None
    return f"{tree.strip()}:{interpreter}"


def _load(root: Path) -> dict[str, dict]:
    try:
        held = json.loads((root / STORE).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return held if isinstance(held, dict) else {}


def passed(root: Path, at: str | None) -> frozenset[str]:
    entry = _load(root).get(at or "")
    names = entry.get("checks") if isinstance(entry, dict) else None
    return frozenset(str(name) for name in names) if isinstance(names, list) else frozenset()


def record(root: Path, at: str | None, names: list[str]) -> None:
    if at is None or not names:
        return
    held = _load(root)
    held[at] = {
        "checks": sorted(set(names) | passed(root, at)),
        "at": datetime.now(UTC).isoformat(),
    }
    kept = dict(sorted(held.items(), key=lambda item: str(item[1].get("at", "")))[-KEEP:])
    path = root / STORE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        marker = path.parent / ".gitignore"
        if not marker.exists():
            marker.write_text("*\n", encoding="utf-8")
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return
