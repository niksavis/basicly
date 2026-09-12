from __future__ import annotations

from pathlib import Path

INSTRUCTIONS_FILE = "AGENTS.md"


def _text_tokens(text: str) -> int:

    return len(text) // 4


def instruction_overhead(repo_root: Path) -> int:

    try:
        path = repo_root / INSTRUCTIONS_FILE
        return _text_tokens(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


SCOPE_EXCLUDED_DIRS: frozenset[str] = frozenset({
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".import_linter_cache",
    ".doctor",
})


def _is_excluded(repo_root: Path, path: Path) -> bool:
    try:
        parts = path.relative_to(repo_root).parts
    except ValueError:
        return True
    return any(part in SCOPE_EXCLUDED_DIRS for part in parts[:-1])


def _scope_files(repo_root: Path, scope: tuple[str, ...]) -> set[Path]:

    files: set[Path] = set()
    for pattern in scope:
        normalized = pattern.strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = normalized.lstrip("/")
        if not normalized:
            continue
        try:
            matches = list(repo_root.glob(normalized))
        except ValueError, NotImplementedError, OSError:
            continue
        for path in matches:
            if path.is_file() and not _is_excluded(repo_root, path):
                files.add(path)
    return files


SCOPE_FILE_READ_CAP = 4_000


def scope_read_cost(repo_root: Path, scope: tuple[str, ...]) -> int:

    total = 0
    for path in _scope_files(repo_root, scope):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += min(_text_tokens(text), SCOPE_FILE_READ_CAP)
    return total
