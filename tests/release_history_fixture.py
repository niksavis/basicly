from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

HISTORY = Path("src/basicly/release_history.json")
RULE = Path(".basicly/core/fragments/rules.yaml")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Release Test",
            "-c",
            "user.email=release@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            *args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


def init(repo: Path) -> Path:
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    return repo


def tag(repo: Path, name: str, text: str, *, annotated: bool = False) -> str:
    path = repo / RULE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"release {name}")
    git(repo, "tag", *(["-a", name, "-m", name] if annotated else [name]))
    return git(repo, "rev-parse", f"refs/tags/{name}^{{commit}}")


def published(repo: Path, destination: Path, tags: Sequence[str]) -> Path:
    git(repo, "init", "-q", "--bare", str(destination))
    git(repo, "remote", "add", "origin", str(destination))
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/main", *(f"refs/tags/{one}" for one in tags))
    return destination


def manifest(repo: Path, tags: Mapping[str, str]) -> Path:
    path = repo / HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "tags": dict(tags)}, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
