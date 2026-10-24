from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess  # nosec B404
from collections.abc import Iterable
from pathlib import Path

from . import __version__, checkout, release_history

DIGESTS_FILE = Path(__file__).with_name("shipped_core.json")
SOURCE_DIGESTS_FILE = Path("src") / "basicly" / DIGESTS_FILE.name
CORE_TREE = ".basicly/core"
SCHEMA_VERSION = 1


def _core_blobs(repo_root: Path, tag: str) -> list[tuple[str, str]]:

    listing = checkout.git(["ls-tree", "-r", "-z", tag, "--", CORE_TREE], cwd=repo_root).stdout
    blobs: list[tuple[str, str]] = []
    for entry in filter(None, listing.split("\0")):
        meta, path = entry.split("\t", 1)
        _mode, kind, oid = meta.split()
        if kind == "blob":
            blobs.append((path.removeprefix(f"{CORE_TREE}/"), oid))
    return blobs


def _blob_digests(repo_root: Path, oids: Iterable[str]) -> dict[str, str]:

    wanted = sorted(set(oids))
    if not wanted:
        return {}
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is not on PATH: the shipped core hashes are read from git tags")
    done = subprocess.run(  # noqa: S603 — resolved git, literal argv; checkout.run decodes text and the digest needs bytes
        [git, "-C", str(repo_root), "cat-file", "--batch"],
        input="".join(f"{oid}\n" for oid in wanted).encode(),
        capture_output=True,
        check=True,
        env=checkout.sanitised_git_env(os.environ),
    )
    digests: dict[str, str] = {}
    stream = done.stdout
    at = 0
    for oid in wanted:
        header_end = stream.index(b"\n", at)
        size = int(stream[at:header_end].split()[2])
        body = stream[header_end + 1 : header_end + 1 + size]
        digests[oid] = "sha256:" + hashlib.sha256(body).hexdigest()
        at = header_end + 1 + size + 1
    return digests


def build_digests(repo_root: Path, version: str) -> dict[str, object]:

    history = release_history.references(repo_root, version)
    pairs = [pair for commit in history.values() for pair in _core_blobs(repo_root, commit)]
    digests = _blob_digests(repo_root, (oid for _path, oid in pairs))
    core: dict[str, set[str]] = {}
    for path, oid in pairs:
        core.setdefault(path, set()).add(digests[oid])
    return {
        "schema_version": SCHEMA_VERSION,
        "tags": list(history),
        "core": {path: sorted(core[path]) for path in sorted(core)},
    }


def render_digests(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=1, sort_keys=True) + "\n"


def write_digests(repo_root: Path, version: str) -> Path:

    target = repo_root / SOURCE_DIGESTS_FILE
    target.write_text(render_digests(build_digests(repo_root, version)), encoding="utf-8")
    return target


def shipped_digests() -> dict[str, frozenset[str]]:

    payload = json.loads(DIGESTS_FILE.read_text(encoding="utf-8"))
    return {path: frozenset(digests) for path, digests in payload["core"].items()}


if __name__ == "__main__":
    print(write_digests(Path.cwd(), __version__))
