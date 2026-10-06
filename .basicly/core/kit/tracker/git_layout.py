from __future__ import annotations

from pathlib import Path

GIT_DIR_NAME = ".git"
_GIT_DIR_MARK = "gitdir:"
DEFAULT_HOOKS_DIR = "hooks"
HOOKS_PATH_KEY = "hookspath"


def git_dir(root: Path) -> Path | None:

    candidate = root / GIT_DIR_NAME
    if candidate.is_dir():
        return candidate
    if candidate.is_file():
        text = candidate.read_text(encoding="utf-8").strip()
        if text.startswith(_GIT_DIR_MARK):
            linked = Path(text[len(_GIT_DIR_MARK) :].strip())
            return linked if linked.is_absolute() else (root / linked).resolve()
    return None


def hooks_dir(root: Path, override: Path | None = None) -> Path | None:

    if override is not None:
        return override
    found = git_dir(root)
    if found is None:
        return None
    common = found / "commondir"
    if common.is_file():
        found = (found / common.read_text(encoding="utf-8").strip()).resolve()
    config = found / "config"
    declared = ""
    if config.is_file():
        for line in config.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip().lower() == HOOKS_PATH_KEY:
                declared = value.strip()
    if not declared:
        return found / DEFAULT_HOOKS_DIR
    path = Path(declared)
    return path if path.is_absolute() else (root / path)
