from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from .checkout import git_common_dir

ARCHIVE_LIMIT_BYTES = 500 * 1024 * 1024

if TYPE_CHECKING:
    from .worktree_contents import RemovalVerdict


def preserve(main: Path, worktree: Path, paths: tuple[Path, ...]) -> Path | None:
    if not paths:
        return None
    parent = git_common_dir(main) / "basicly-preserved"
    if parent.is_symlink():
        raise RuntimeError("generated artifact preservation directory must not be a symlink")
    held = sum(path.stat().st_size for path in parent.rglob("*") if path.is_file())
    if held > ARCHIVE_LIMIT_BYTES:
        raise SystemExit(
            f"cleanup refuses to archive more: {parent} already holds {held // (1024 * 1024)} MB, "
            f"over the {ARCHIVE_LIMIT_BYTES // (1024 * 1024)} MB limit; inspect it, move or remove "
            "the archives you no longer need, then run the cleanup again"
        )
    destination = parent / f"{worktree.name}-{uuid.uuid4().hex}"
    destination.mkdir(parents=True, exist_ok=False)
    files = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"generated artifact changed before preservation: {path}")
        data = path.read_bytes()
        files.append({
            "path": path.relative_to(worktree).as_posix(),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "preserved": False,
        })
    inventory = {"schema": "basicly-worktree-preservation/v1", "files": files}
    manifest = destination / "manifest.json"
    manifest.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    for path, entry in zip(paths, files, strict=True):
        target = destination / str(entry["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        copied = b"" if target.is_symlink() else target.read_bytes()
        if (
            target.is_symlink()
            or len(copied) != entry["bytes"]
            or hashlib.sha256(copied).hexdigest() != entry["sha256"]
        ):
            raise RuntimeError(f"preserved artifact changed; inspect {manifest}")
        entry["preserved"] = True
        manifest.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    print(f"Preserved {len(files)} generated artifact(s), with inventory: {manifest}")
    return manifest


def matches(worktree: Path, verdict: RemovalVerdict, manifest: Path | None) -> bool:
    if manifest is None:
        return not verdict.preserve
    entries = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    expected = {entry["path"]: entry["sha256"] for entry in entries if entry["preserved"]}
    actual = {
        path.relative_to(worktree).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in verdict.preserve
    }
    return len(expected) == len(entries) and expected == actual
