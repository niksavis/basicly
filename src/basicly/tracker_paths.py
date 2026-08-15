"""The one resolver for br's ``.beads/redirect`` (basicly-tcmy.19).

Below :mod:`basicly.br` because :mod:`basicly.tracker_usage` cannot import br without
a cycle; four copies had split the tracker read from the ledger write.
"""

from __future__ import annotations

from pathlib import Path


def beads_dir(repo_root: Path) -> Path:
    """*repo_root*'s beads dir, following br's git-ignored ``redirect`` file.

    Any directory the redirect names is honoured, as ``beads-commit-msg.py`` does:
    a pre-check owes its gate's answer. An empty file names none: ``Path("")`` is
    ``Path(".")``.
    """
    beads = Path(repo_root) / ".beads"
    redirect = beads / "redirect"
    if redirect.is_file():
        try:
            named = redirect.read_text(encoding="utf-8").strip()
        except OSError:
            return beads
        if not named:
            return beads
        target = Path(named)
        if target.is_dir():
            return target
    return beads
