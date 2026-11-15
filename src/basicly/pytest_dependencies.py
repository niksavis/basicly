from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from . import pyright_inputs

_IGNORED = frozenset({"__pycache__", ".cache"})


def _validate(path: Path, resolved: Path, canonical: Path, allowed: tuple[Path, ...]) -> None:
    if not resolved.is_relative_to(canonical):
        if path.suffix == ".pth" or not resolved.is_file():
            raise pyright_inputs.UnsupportedInputsError("external pytest dependency")
    elif path.suffix == ".pth":
        for line in path.read_text().splitlines():
            if line.strip() and not line.startswith(("#", "import ", "import\t")):
                target = (path.parent / line.strip()).resolve()
                if target not in allowed and not target.is_relative_to(canonical):
                    raise pyright_inputs.UnsupportedInputsError("external pytest import path")


def fingerprints(
    roots: Sequence[Path], workspace: Path | None = None, *, engine: Path | None = None
) -> dict[Path, str]:
    roots = tuple(dict.fromkeys(roots))
    canonical = {root: root.resolve() for root in roots}
    digests = {root: hashlib.sha256() for root in roots}
    allowed = tuple(
        path
        for path in (engine, workspace, workspace / "src" if workspace is not None else None)
        if path is not None
    )
    groups: dict[Path, list[Path]] = {}
    for root in roots:
        parents = [
            other
            for other in roots
            if root.is_relative_to(other)
            and not (_IGNORED | {".."}).intersection(
                part.casefold() for part in root.relative_to(other).parts
            )
        ]
        parent = min(parents, key=lambda path: len(path.parts))
        groups.setdefault(parent, []).append(root)

    for parent, members in groups.items():
        for directory, dirs, files in parent.walk(on_error=pyright_inputs._unreadable):
            dirs[:] = sorted(name for name in dirs if name not in _IGNORED)
            if any((directory / name).is_symlink() for name in dirs):
                raise pyright_inputs.UnsupportedInputsError("symlinked pytest dependency directory")
            active = [root for root in members if directory.is_relative_to(root)]
            for name in sorted(files):
                path = directory / name
                if name.startswith(".env"):
                    raise pyright_inputs.UnsupportedInputsError("protected pytest dependency")
                resolved = path.resolve()
                for root in active:
                    _validate(path, resolved, canonical[root], allowed)
                actual = path if resolved.is_relative_to(canonical[parent]) else resolved
                content = hashlib.sha256(actual.read_bytes()).digest()
                for root in active:
                    if resolved.is_relative_to(canonical[root]):
                        digests[root].update(path.relative_to(root).as_posix().encode() + b"\0")
                    else:
                        digests[root].update(
                            f"{path.relative_to(root).as_posix()}\0{resolved}\0".encode()
                        )
                    digests[root].update(content)
    if any(root.resolve() != canonical[root] for root in roots):
        raise pyright_inputs.UnsupportedInputsError("pytest dependency root changed")
    return {root: digest.hexdigest() for root, digest in digests.items()}
