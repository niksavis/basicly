"""Sibling git-worktree isolation for the basicly harness.

A harness track works in an isolated sibling worktree so parallel work never
collides in one checkout. ``create`` carves ``<repo>.worktrees/<name>`` off a
base branch onto ``harness/<name>``, provisions its own standalone ``.venv`` /
``node_modules`` (via ``uv sync`` / ``npm install``, never symlinks — so a
later removal can never follow a link back into the main checkout), and
installs the repo's git hooks so the same gates run there.

Session metadata lives in the git *common* dir (shared by every linked
worktree, never committed), so a worktree checkout and the main checkout read
the same records without a tracked file.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .hooks import PRECOMMIT_CONFIG, hook_stages, install_hooks, load_hook_specs

BRANCH_PREFIX = "harness/"

# Heavy dependency dirs each worktree gets as its own standalone tree. They are
# freshly installed (not symlinked/copied from main), which keeps the worktree
# self-contained and makes teardown safe.
DEP_DIRS = (".venv", "node_modules")


def run(
    args: list[str],
    *,
    cwd: Path | str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with explicit utf-8 decoding (Windows defaults to cp1252)."""
    proc = subprocess.run(  # nosec B603
        args,
        cwd=cwd,
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


def now_iso() -> str:
    """Return the current local time as an ISO-8601 string."""
    return datetime.now(UTC).astimezone().isoformat()


@dataclass
class Session:
    """Persistent record of one worktree (stored in the git common dir)."""

    name: str
    branch: str
    base: str
    base_head: str
    worktree_path: str
    created_at: str

    @property
    def path(self) -> Path:
        """Return the worktree location as a :class:`~pathlib.Path`."""
        return Path(self.worktree_path)


def sessions_dir(cwd: Path | str | None = None) -> Path:
    """Return (creating if needed) the common-dir directory of session records."""
    directory = git_common_dir(cwd) / "basicly-worktrees"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def session_file(name: str, cwd: Path | str | None = None) -> Path:
    """Return the JSON session-record path for worktree *name*."""
    return sessions_dir(cwd) / f"{name}.json"


def save_session(session: Session, cwd: Path | str | None = None) -> None:
    """Persist *session* to its record in the git common dir."""
    session_file(session.name, cwd).write_text(
        json.dumps(asdict(session), indent=2) + "\n", encoding="utf-8"
    )


def load_session(name: str, cwd: Path | str | None = None) -> Session | None:
    """Load the session record for *name*, or ``None`` when there is none."""
    path = session_file(name, cwd)
    if not path.exists():
        return None
    return Session(**json.loads(path.read_text(encoding="utf-8")))


def list_sessions(cwd: Path | str | None = None) -> list[Session]:
    """Return all recorded worktree sessions, sorted by name."""
    out: list[Session] = []
    for path in sorted(sessions_dir(cwd).glob("*.json")):
        out.append(Session(**json.loads(path.read_text(encoding="utf-8"))))
    return out


def provision_deps(worktree: Path) -> list[str]:
    """Install standalone ``.venv`` / ``node_modules`` inside *worktree*.

    Runs ``uv sync`` when a Python project manifest is present and
    ``npm install`` when ``package.json`` is present. Each produces a real,
    self-contained tree (never a symlink to main), so the worktree runs its own
    gates and teardown stays safe. Returns a status note per ecosystem acted on.
    """
    notes: list[str] = []
    if (worktree / "pyproject.toml").exists() or (worktree / "uv.lock").exists():
        run(["uv", "sync"], cwd=worktree)
        notes.append(".venv: uv sync")
    if (worktree / "package.json").exists():
        run(["npm", "install"], cwd=worktree)
        notes.append("node_modules: npm install")
    return notes


def install_worktree_hooks(worktree: Path) -> str:
    """Install the repo's git hooks for every stage the harness defines."""
    stages = hook_stages(load_hook_specs())
    if not stages:
        return "hooks: none defined"
    ok, message = install_hooks(worktree, stages)
    prefix = "hooks" if ok else "hooks (FAILED)"
    return f"{prefix}: {', '.join(stages)} — {message}"


def create(name: str, base: str | None = None) -> Session:
    """Create and provision a sibling worktree for *name*.

    Adds ``<repo>.worktrees/<name>`` on a new ``harness/<name>`` branch off
    *base* (default: the current branch), provisions its own dependency trees
    and git hooks, and records a session in the git common dir.
    """
    base = base or current_branch()
    branch = f"{BRANCH_PREFIX}{name}"
    worktree = worktrees_root() / name

    if worktree.exists():
        raise SystemExit(f"worktree path already exists: {worktree}")
    if load_session(name) is not None:
        raise SystemExit(f"a worktree named {name!r} already exists; clean it up first")

    base_head = git(["rev-parse", "--short", base]).stdout.strip()
    worktree.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", str(worktree), "-b", branch, base])

    notes = provision_deps(worktree)

    env_local = main_checkout() / ".env.local"
    if env_local.exists():
        (worktree / ".env.local").write_text(
            env_local.read_text(encoding="utf-8"), encoding="utf-8"
        )
        notes.append(".env.local: copied")

    notes.append(install_worktree_hooks(worktree))

    session = Session(
        name=name,
        branch=branch,
        base=base,
        base_head=base_head,
        worktree_path=str(worktree),
        created_at=now_iso(),
    )
    save_session(session)

    print(f"Created worktree {name!r}")
    print(f"  path:   {worktree}")
    print(f"  branch: {branch}  (base {base} @ {base_head})")
    for note in notes:
        print(f"  {note}")
    return session


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


def _resolve_worktree(name: str, main: Path) -> tuple[Path, str | None]:
    """Return ``(worktree_path, branch)`` for *name*.

    Prefers the session record; falls back to ``git worktree list`` so a
    worktree with no session (e.g. one made by raw ``git worktree add``) can
    still be cleaned up safely. *name* matches a registered path or its
    directory basename.
    """
    session = load_session(name)
    if session is not None:
        return session.path, session.branch

    target = Path(name)
    for path, branch in registered_worktrees(main).items():
        if path == target or path.name == name:
            return path, branch
    raise SystemExit(
        f"no worktree named {name!r}: no session record and no registered worktree "
        f"matches it. Run `git worktree list` to see them."
    )


def stale_sessions(cwd: Path | str | None = None) -> list[Session]:
    """Return sessions whose worktree directory no longer exists on disk.

    A stale record is left when a worktree is removed out-of-band; ``cleanup``
    still reclaims its branch and metadata.
    """
    return [s for s in list_sessions(cwd) if not s.path.exists()]


def cleanup(name: str, *, force: bool = False) -> None:
    """Remove worktree *name* and delete its merged branch.

    Removes the worktree directory (``git worktree remove --force`` — the
    provisioned deps are untracked, so a plain remove would refuse), prunes the
    registry, deletes the ``harness/<name>`` branch, and drops the session
    record. The base branch is never touched. ``force`` deletes the branch even
    if unmerged (``git branch -D``); by default an unmerged branch is left with
    a note instead of being lost. Reclaims a stale record whose worktree dir is
    already gone.

    Finishes by reinstalling the base checkout's hooks: worktrees share the
    common ``.git/hooks`` dir, so the shims installed during provisioning can
    embed the worktree venv's pre-commit path, which dangles once that venv is
    deleted (every base-checkout commit would fail until hooks are reinstalled).
    """
    main = main_checkout()
    worktree, branch = _resolve_worktree(name, main)

    if worktree.exists():
        git(["worktree", "remove", "--force", str(worktree)], cwd=main)
    git(["worktree", "prune"], cwd=main, check=False)

    if (main / PRECOMMIT_CONFIG).exists():
        print(f"  {install_worktree_hooks(main)}")

    branch_removed = True
    if branch:
        delete_flag = "-D" if force else "-d"
        deleted = git(["branch", delete_flag, branch], cwd=main, check=False)
        branch_removed = deleted.returncode == 0
        if not branch_removed:
            detail = (deleted.stderr or deleted.stdout).strip()
            print(f"  note: branch {branch} not deleted ({detail})")

    # Keep the record when an unmerged branch survives, so `cleanup --force`
    # can still find and reclaim it once the worktree dir is already gone.
    if branch_removed:
        session_file(name).unlink(missing_ok=True)
        print(f"Cleaned up worktree {name!r} (worktree + branch + metadata).")
    else:
        print(
            f"Removed worktree {name!r}; kept branch {branch} and its record "
            "(unmerged — re-run with force to reclaim)."
        )
