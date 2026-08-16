"""Where a checkout stands in git's worktree layout, and where its siblings are.

One responsibility: answering positional questions about a working tree — which
git common dir it shares, which checkout is the primary one, where the sibling
``<repo>.worktrees`` directory sits, what branch is out, whether this path is a
linked worktree, and which worktrees git currently tracks. Every answer is a
``git rev-parse``/``worktree list`` reading, so :func:`run` and :func:`git` live
here as the single spelling those readings go through rather than as a second
module nothing else would ever import.

Why the answers need a home of their own: they are asked from all over the
engine, by callers that have nothing to do with provisioning a worktree.
``release`` refuses to publish from a linked checkout, ``loop`` refuses its
merge/ship transitions there, ``commit`` resolves the primary tree to find the
tracker, and ``merge`` needs the current branch — none of which is the
create/reclaim lifecycle.

The one non-obvious rule is that ``git rev-parse`` may answer with a path
relative to *cwd*, so every path here is resolved against the *cwd* it was asked
about before being returned; comparing an unresolved answer to an absolute path
is how :func:`is_linked_checkout` would silently report every checkout as linked.

Split out of ``worktree`` when the module-size ratchet caught that module
growing. The boundary is *where* against *lifecycle*: nothing here creates,
provisions, records or removes anything, which is all :mod:`basicly.worktree`
does, and that is why this module needs no import back into it.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

# pre-commit's `no_git_env` allowlist, minus the `GIT_CONFIG_*` trio it forwards to hooks.
GIT_ENV_KEPT = frozenset({
    "GIT_ALLOW_PROTOCOL",
    "GIT_ASKPASS",
    "GIT_EXEC_PATH",
    "GIT_HTTP_PROXY_AUTHMETHOD",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GIT_SSL_CAINFO",
    "GIT_SSL_NO_VERIFY",
})


def sanitised_git_env(env: Mapping[str, str]) -> dict[str, str]:
    """*env* without the inherited `GIT_*` that would outrank a caller's *cwd*.

    Git reads `GIT_DIR` before *cwd*, and exports it into every hook it runs from a
    checkout whose git dir is not a plain `<worktree>/.git` — every lane worktree. A
    harness running from a hook therefore aims every call below at the wrong repository
    unless it is dropped here (basicly-e2mz.16).
    """
    return {
        name: value
        for name, value in env.items()
        if not name.startswith("GIT_") or name in GIT_ENV_KEPT
    }


# An operator's terminal preference, which a dispatched lane must never inherit: rich
# reads these when it builds a Console, so a forced value fails a verify run on ANSI
# escapes in code that has nothing to do with colour (basicly-e2mz.34). `NO_COLOR` is
# deliberately absent — it only ever moves output toward the plain form gates assert.
COLOUR_ENV_FORCING = frozenset({"FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR", "COLORTERM"})


def sanitised_colour_env(env: Mapping[str, str]) -> dict[str, str]:
    """*env* without the colour-forcing variables a developer's shell may carry."""
    return {name: value for name, value in env.items() if name not in COLOUR_ENV_FORCING}


def run(
    args: list[str],
    *,
    cwd: Path | str | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with explicit utf-8 decoding (Windows defaults to cp1252).

    *env* replaces the child's environment wholesale when given (the release
    regeneration needs PYTHONPATH pointed at the repo being released); omitting it
    inherits this process's, which is what every other caller wants. Either way
    :func:`sanitised_git_env` runs on it, so *cwd* decides the repository.
    """
    # Validating `args` here — the alternative to the suppression — would duplicate the
    # naming rules each caller already enforces, and `shell=False` already makes a branch
    # or worktree name one argv element rather than shell text.
    proc = subprocess.run(  # noqa: S603 — argv list built by this module, no shell
        args,
        cwd=cwd,
        env=sanitised_git_env(os.environ if env is None else env),
        check=False,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(map(str, args))}\n{detail}"
        )
    return proc


def git(
    args: list[str], *, cwd: Path | str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run ``git`` with the shared utf-8 subprocess wrapper."""
    return run(["git", *args], cwd=cwd, check=check)


def git_common_dir(cwd: Path | str | None = None) -> Path:
    """Return the shared git common dir (``<main>/.git`` for the main checkout)."""
    out = git(["rev-parse", "--git-common-dir"], cwd=cwd).stdout.strip()
    path = Path(out)
    if not path.is_absolute():
        path = Path(cwd or Path.cwd()) / path
    return path.resolve()


def main_checkout(cwd: Path | str | None = None) -> Path:
    """Return the primary working tree (parent of the git common dir)."""
    return git_common_dir(cwd).parent


def worktrees_root(cwd: Path | str | None = None) -> Path:
    """Return the sibling ``<repo>.worktrees`` directory that holds worktrees."""
    main = main_checkout(cwd)
    return main.parent / f"{main.name}.worktrees"


def current_branch(cwd: Path | str | None = None) -> str:
    """Return the checked-out branch name for *cwd*."""
    return git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd).stdout.strip()


def is_linked_checkout(cwd: Path | str | None = None) -> bool:
    """True when *cwd* is inside a linked worktree rather than the primary checkout.

    A linked worktree has its own per-worktree git dir under
    ``<common>/worktrees/<name>``; the primary checkout's git dir *is* the common
    dir. Comparing the two is git's own definition of "am I in a linked worktree",
    which the loop uses to refuse merge/ship transitions that must run from base.
    Returns ``False`` when *cwd* is not a git repository (nothing to refuse).
    """
    proc = git(["rev-parse", "--git-dir"], cwd=cwd, check=False)
    if proc.returncode != 0:
        return False
    git_dir = Path(proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = Path(cwd or Path.cwd()) / git_dir
    return git_dir.resolve() != git_common_dir(cwd)


def registered_worktrees(cwd: Path | str | None = None) -> dict[Path, str | None]:
    """Return ``{path: branch}`` for every worktree git currently tracks.

    Branch is ``None`` for a detached-HEAD worktree. Used to resolve and to
    reconcile against session records.
    """
    out: dict[Path, str | None] = {}
    porcelain = git(["worktree", "list", "--porcelain"], cwd=cwd).stdout
    path: Path | None = None
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :].strip())
            out[path] = None
        elif line.startswith("branch ") and path is not None:
            out[path] = line[len("branch ") :].strip().removeprefix("refs/heads/")
    return out
