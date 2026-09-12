from __future__ import annotations

from pathlib import Path

from . import redact

LOG_DIR = Path(".basicly/usage")

TAIL_BYTES = 60_000


def log_path(repo_root: Path, check: str) -> Path:

    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in check)
    return repo_root / LOG_DIR / f"verify-fail-{safe or 'check'}.log"


def write(repo_root: Path, check: str, output: str) -> Path | None:

    if not output.strip():
        return None
    kept = output[-TAIL_BYTES:]
    if len(output) > TAIL_BYTES:
        kept = f"[the first {len(output) - TAIL_BYTES} characters are not kept]\n{kept}"
    text = redact.redact_machine_identity(redact.redact_secrets(kept))
    path = log_path(repo_root, check)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return path


def pointer(path: Path | None, repo_root: Path) -> str:

    if path is None:
        return ""
    try:
        named = path.relative_to(repo_root)
    except ValueError:
        named = path
    return f"what it said: {named.as_posix()}"
