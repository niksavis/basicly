from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import checkout

SOURCE_FILE = Path("src/basicly/release_history.json")
SCHEMA_VERSION = 1
TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
COMMIT_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class HistoryError(ValueError):
    pass


@dataclass(frozen=True)
class HistoryPlan:
    tags: dict[str, str]
    added: tuple[str, ...]


def version_key(tag: str) -> tuple[int, int, int]:
    found = TAG_PATTERN.fullmatch(tag)
    if found is None:
        raise HistoryError(f"release tag {tag!r} must be vX.Y.Z")
    major, minor, patch = map(int, found.groups())
    return major, minor, patch


def _checked_tags(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise HistoryError(f"{SOURCE_FILE.as_posix()} must declare a tags object")
    for tag, commit in value.items():
        if not isinstance(tag, str) or TAG_PATTERN.fullmatch(tag) is None:
            raise HistoryError(f"canonical release tag {tag!r} must be vX.Y.Z")
        if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
            raise HistoryError(f"canonical release {tag} must name a complete commit hash")
    return {tag: value[tag] for tag in sorted(value, key=version_key)}


def read(repo_root: Path) -> dict[str, str]:
    try:
        payload = json.loads((repo_root / SOURCE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HistoryError(
            f"cannot read {SOURCE_FILE.as_posix()}; restore the committed canonical release history"
        ) from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "tags"}
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != SCHEMA_VERSION
    ):
        raise HistoryError(
            f"{SOURCE_FILE.as_posix()} must use release history schema {SCHEMA_VERSION}"
        )
    return _checked_tags(payload["tags"])


def write(repo_root: Path, tags: Mapping[str, str]) -> Path:
    checked = _checked_tags(dict(tags))
    path = repo_root / SOURCE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "tags": checked}, indent=1, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return path


def validate_local(repo_root: Path, tags: Mapping[str, str]) -> None:
    for tag, expected in tags.items():
        found = checkout.git(
            ["rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"], cwd=repo_root, check=False
        )
        if found.returncode != 0 or found.stdout.strip() != expected:
            raise HistoryError(
                f"canonical release {tag} is missing or moved locally; "
                "run `git fetch origin --tags` "
                "and verify its published commit before regenerating inventory"
            )


def references(repo_root: Path, version: str) -> dict[str, str]:
    bound = version_key(f"v{version}")
    tags = {tag: commit for tag, commit in read(repo_root).items() if version_key(tag) < bound}
    validate_local(repo_root, tags)
    return tags


def advertised(repo_root: Path) -> dict[str, str]:
    try:
        output = checkout.git(["ls-remote", "--tags", "origin"], cwd=repo_root).stdout
    except (RuntimeError, OSError) as exc:
        raise HistoryError(
            "cannot audit origin release tags; check `git ls-remote --tags origin` before releasing"
        ) from exc
    direct: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            raise HistoryError("origin returned a malformed release-tag advertisement")
        commit, ref = parts
        if not ref.startswith("refs/tags/"):
            raise HistoryError("origin returned a ref outside its advertised tags")
        name = ref.removeprefix("refs/tags/")
        tag = name.removesuffix("^{}")
        if TAG_PATTERN.fullmatch(tag) is None:
            continue
        if COMMIT_PATTERN.fullmatch(commit) is None:
            raise HistoryError(f"origin advertised an invalid object hash for {tag}")
        selected = peeled if name.endswith("^{}") else direct
        if tag in selected and selected[tag] != commit:
            raise HistoryError(f"origin advertised conflicting objects for {tag}")
        selected[tag] = commit
    if set(peeled) - set(direct):
        raise HistoryError("origin advertised a peeled release without its tag ref")
    return {tag: peeled.get(tag, direct[tag]) for tag in sorted(direct, key=version_key)}


def audit(repo_root: Path, version: str) -> HistoryPlan:
    recorded = read(repo_root)
    remote = advertised(repo_root)
    for tag, commit in recorded.items():
        if remote.get(tag) != commit:
            raise HistoryError(
                f"published release {tag} is missing or moved on origin; "
                "recorded history must remain immutable"
            )
    if f"v{version}" in remote:
        raise HistoryError(f"release v{version} is already published on origin")
    bound = version_key(f"v{version}")
    additions = {
        tag: commit
        for tag, commit in remote.items()
        if version_key(tag) < bound and tag not in recorded
    }
    planned = _checked_tags({**recorded, **additions})
    validate_local(repo_root, planned)
    return HistoryPlan(planned, tuple(additions))
