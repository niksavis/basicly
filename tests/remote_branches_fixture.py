from __future__ import annotations

import os
import subprocess  # nosec B404
from pathlib import Path

_ID = ["-c", "user.name=t", "-c", "user.email=t@t"]


def _git(where: Path, *args: str) -> str:
    done = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(where), *_ID, *args],
        capture_output=True,
        text=True,
        check=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    )
    return done.stdout.strip()


def seeded_main(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    checkout = tmp_path / "main"
    _git(tmp_path, "clone", "-q", str(origin), str(checkout))
    (checkout / "a.txt").write_text("a\n", encoding="utf-8")
    _git(checkout, "add", "a.txt")
    _git(checkout, "commit", "-q", "-m", "a")
    _git(checkout, "push", "-q", "origin", "main")
    return checkout


def _lane(main: Path, name: str, path: str) -> str:
    _git(main, "switch", "-q", "-c", name, "origin/main")
    (main / path).write_text(f"{name}\n", encoding="utf-8")
    _git(main, "add", path)
    _git(main, "commit", "-q", "-m", name)
    _git(main, "push", "-q", "origin", name)
    head = _git(main, "rev-parse", "HEAD")
    _git(main, "switch", "-q", "main")
    return head


def _on_origin(main: Path, ref: str) -> bool:
    return bool(_git(main, "ls-remote", "--heads", "origin", ref))
