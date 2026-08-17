"""The one resolver for *which checkout owns the tracker* (basicly-tcmy.19).

**One store per repository, never one per worktree.** A write that landed in a lane's own
checkout would be deleted with the worktree at teardown, which is what happened to the
usage spool (basicly-vkh0.8). Provisioning writes a one-line, git-ignored ``redirect``
naming the base checkout; four copies of the rule for reading it had disagreed in exactly
the redirected case, so every caller comes through here.

Below :mod:`basicly.tracker` because :mod:`basicly.tracker_usage` cannot import that module
without a cycle.
"""

# comment-density-waiver: three one-line resolvers over 130 tokens of code, so the share
# is set by the member count and not by narration — the same shape as `label_source`. The
# prose is the incident behind the rule and the two ways a redirect resolves to nothing,
# neither of which a reader gets from the code.

from __future__ import annotations

from pathlib import Path

LEDGER_DIR_NAME = Path(".basicly") / "ledger"
REDIRECT_NAME = "redirect"


def tracker_root(repo_root: Path) -> Path:
    """The checkout owning *repo_root*'s tracker: itself, or a redirect target.

    Honoured only when the target exists, as ``tracker-commit-msg.py`` does — a pre-check
    owes its gate's answer. An empty file names none: ``Path("")`` is ``Path(".")``.
    """
    root = Path(repo_root)
    redirect = root / LEDGER_DIR_NAME / REDIRECT_NAME
    if not redirect.is_file():
        return root
    try:
        named = redirect.read_text(encoding="utf-8").strip()
    except OSError:
        return root
    if not named:
        return root
    target = Path(named)
    return target if target.is_dir() else root


def ledger_dir(repo_root: Path) -> Path:
    """The owned ledger's directory for *repo_root*, after the redirect."""
    return tracker_root(repo_root) / LEDGER_DIR_NAME
