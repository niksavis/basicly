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

from .br import try_run_br
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
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with explicit utf-8 decoding (Windows defaults to cp1252).

    *env* replaces the child's environment wholesale when given (the release
    regeneration needs PYTHONPATH pointed at the repo being released); omitting it
    inherits this process's, which is what every other caller wants.
    """
    proc = subprocess.run(  # nosec B603
        args,
        cwd=cwd,
        env=env,
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


def _probe_redirect(name: str, worktree: Path, base_beads: Path) -> None:
    """Fail fast when the installed br does not honor ``.beads/redirect``.

    A br without redirect support ignores the file, auto-imports the
    worktree's checked-out ``issues.jsonl`` into a fresh local DB, and
    silently runs a divergent tracker — lost gates and claims. A missing br
    or a base that is not a br workspace skips the probe (both are supported
    states); the probe rejects only a br that answers with the wrong dir.
    """
    proc = try_run_br(worktree, ["where", "--json"])
    if proc is None or proc.returncode != 0:
        return
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict) or "path" not in data:
        return
    resolved = Path(str(data["path"]))
    if resolved.resolve() != base_beads.resolve():
        raise SystemExit(
            f"the installed br ignored .beads/redirect and resolved {resolved} — "
            "worktree tracker sharing needs a redirect-capable br (0.2.16 is the "
            "known-good floor). Run `br upgrade`, then "
            f"`basicly worktree cleanup {name} --force` and recreate the worktree."
        )


def create(name: str, base: str | None = None, repo_root: Path | str | None = None) -> Session:
    """Create and provision a sibling worktree for *name*.

    Adds ``<repo>.worktrees/<name>`` on a new ``harness/<name>`` branch off
    *base* (default: the current branch), provisions its own dependency trees
    and git hooks, and records a session in the git common dir. The worktree's
    ``.beads`` is redirected at the base checkout's (br's git-ignored
    ``redirect`` file), so every tracker read/write from the worktree — br
    itself and the beads commit-msg hook alike — hits the one shared DB/JSONL:
    no divergent copy, nothing to reconcile at landing. The checked-out
    ``issues.jsonl`` is deliberately left untouched; overwriting it with the
    base working-tree version would leave the worktree permanently dirty and
    block the landing rebase.

    *repo_root* names the repository to operate on, like every other engine
    module; it defaults to the process cwd, which is where the CLI always runs.
    Passing it is what keeps a caller that stands somewhere else — a driver, an
    integration test against a fixture repo — from provisioning into whichever
    checkout the process happens to be in.
    """
    base = base or current_branch(repo_root)
    branch = f"{BRANCH_PREFIX}{name}"
    worktree = worktrees_root(repo_root) / name

    if worktree.exists():
        raise SystemExit(f"worktree path already exists: {worktree}")
    if load_session(name, repo_root) is not None:
        raise SystemExit(f"a worktree named {name!r} already exists; clean it up first")

    base_head = git(["rev-parse", "--short", base], cwd=repo_root).stdout.strip()
    worktree.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", str(worktree), "-b", branch, base], cwd=repo_root)

    # Tracker sharing first (before the slow dep install), so a br that cannot
    # follow the redirect fails the provisioning fast.
    notes: list[str] = []
    main = main_checkout(repo_root)
    base_beads = main / ".beads"
    if base_beads.is_dir():
        target_beads = worktree / ".beads"
        target_beads.mkdir(parents=True, exist_ok=True)
        # Machine-local, git-ignored by br's own .beads/.gitignore — an
        # absolute path here never reaches a commit.
        (target_beads / "redirect").write_text(f"{base_beads}\n", encoding="utf-8")
        notes.append(".beads/redirect: tracker shared with the base checkout")
        _probe_redirect(name, worktree, base_beads)

    notes += provision_deps(worktree)

    env_local = main / ".env.local"
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
    save_session(session, repo_root)

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


def _resolve_worktree(
    name: str, main: Path, repo_root: Path | str | None = None
) -> tuple[Path, str | None]:
    """Return ``(worktree_path, branch)`` for *name*.

    Prefers the session record; falls back to ``git worktree list`` so a
    worktree with no session (e.g. one made by raw ``git worktree add``) can
    still be cleaned up safely. *name* matches a registered path or its
    directory basename.
    """
    session = load_session(name, repo_root)
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


@dataclass(frozen=True)
class RemovalVerdict:
    """Whether a worktree's tree may be discarded, and what stands in the way.

    ``holds`` is the human-readable reason to refuse — pending work, or the fact
    that it could not be determined. Empty exactly when ``may_remove`` is true.
    """

    may_remove: bool
    holds: str
    # True when git could not answer, as distinct from answering "there is work
    # here". Both refuse; only this one is a broken query rather than a full tree,
    # and the operator needs to be told which (basicly-jr0l.47).
    indeterminate: bool = False


def classify_worktree_tree(returncode: int, stdout: str) -> RemovalVerdict:
    """Classify a ``git status --porcelain`` result from a worktree, fail-closed.

    Pure: takes the raw result, touches nothing, so every branch below is directly
    testable — which is the point, because the cost of getting one wrong is
    committed work that no longer exists.

    **A transient git error must never authorize a deletion** (basicly-jr0l.47). A
    lock held by a concurrent lane, an interrupted index write, or a filesystem
    hiccup makes this query fail; reading that as "nothing to keep" hands
    ``git worktree remove --force`` a tree it was never allowed to discard. So a
    non-zero exit holds the worktree instead of clearing it. Holding costs disk;
    deleting costs work, and only one of the two is recoverable.

    An unparsable porcelain line holds for the same reason: the format is
    ``XY <path>``, so anything shorter carries a status this cannot read, and a
    status it cannot read may be the one that matters.

    The provisioned dep dirs and the tracker export (which provisioning syncs from
    base) are expected noise rather than work, and never hold a teardown.
    """
    if returncode != 0:
        return RemovalVerdict(
            may_remove=False,
            holds=(
                f"git status could not be read in the worktree (exit {returncode}); "
                "refusing to remove a tree whose contents are unknown — a lock held by "
                "a concurrent lane is the usual cause, so retry, or pass force to "
                "discard the tree regardless"
            ),
            indeterminate=True,
        )
    expected_noise = (*DEP_DIRS, ".beads")
    noise_prefixes = tuple(f"{d}/" for d in expected_noise)
    pending: list[str] = []
    unparsable: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if len(line) < 4:  # "XY p" is the shortest a real entry can be
            unparsable.append(line)
            continue
        path = line[3:].strip().strip('"')
        if path in expected_noise or path.startswith(noise_prefixes):
            continue
        pending.append(line)
    if unparsable:
        return RemovalVerdict(
            may_remove=False,
            holds=(
                "git status returned a line this cannot parse, so the worktree's state "
                "is unknown; refusing to remove it:\n" + "\n".join(unparsable)
            ),
            indeterminate=True,
        )
    if pending:
        return RemovalVerdict(may_remove=False, holds="\n".join(pending))
    return RemovalVerdict(may_remove=True, holds="")


def _worktree_removal_verdict(worktree: Path) -> RemovalVerdict:
    """Ask git about *worktree*'s tree and classify the answer, fail-closed.

    The only impure part: a git failure that raises (git absent, unreadable path)
    is caught and classified as indeterminate rather than escaping, so no caller can
    reach a removal by way of an exception it swallowed elsewhere.
    """
    try:
        proc = git(["status", "--porcelain"], cwd=worktree, check=False)
    except OSError, RuntimeError:
        return RemovalVerdict(
            may_remove=False,
            holds=(
                "git could not be run in the worktree, so its contents are unknown; "
                "refusing to remove it (pass force to discard the tree regardless)"
            ),
            indeterminate=True,
        )
    return classify_worktree_tree(proc.returncode, proc.stdout)


def cleanup(name: str, *, force: bool = False, repo_root: Path | str | None = None) -> None:
    """Remove worktree *name* and delete its merged branch.

    Removes the worktree directory (``git worktree remove --force`` — the
    provisioned deps are untracked, so a plain remove would refuse), prunes the
    registry, deletes the ``harness/<name>`` branch, and drops the session
    record. The base branch is never touched. Refuses when the worktree holds
    uncommitted changes beyond the dep dirs and tracker export — ``--force``
    discards them. ``force`` also deletes the branch even if unmerged
    (``git branch -D``); by default an unmerged branch is left with a note
    instead of being lost. Reclaims a stale record whose worktree dir is
    already gone.

    Finishes by reinstalling the base checkout's hooks: worktrees share the
    common ``.git/hooks`` dir, so the shims installed during provisioning can
    embed the worktree venv's pre-commit path, which dangles once that venv is
    deleted (every base-checkout commit would fail until hooks are reinstalled).

    *repo_root* selects the repository, as in :func:`create`; it defaults to the
    process cwd.
    """
    main = main_checkout(repo_root)
    worktree, branch = _resolve_worktree(name, main, repo_root)

    if worktree.exists():
        verdict = _worktree_removal_verdict(worktree)
        if not verdict.may_remove and not force:
            if verdict.indeterminate:
                # Not "has changes" — "cannot tell", which is a different thing to
                # tell the operator and a different thing to do about it
                # (basicly-jr0l.47).
                raise SystemExit(f"worktree {name!r} not removed: {verdict.holds}")
            raise SystemExit(
                f"worktree {name!r} has uncommitted changes; commit them or pass "
                f"force to discard:\n{verdict.holds}"
            )
        if verdict.indeterminate:
            # Forced past an unknown state: say so, so a discarded tree is never a
            # surprise found later.
            print(f"  warning: forcing removal of {name!r} despite {verdict.holds}")
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
            # A branch that is already gone (e.g. deleted by hand during a manual
            # recovery) is effectively removed — treat it so, or its session
            # record is stranded and keeps counting toward the concurrency cap.
            exists = git(
                ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                cwd=main,
                check=False,
            )
            # Only exit 1 means "the ref is absent". Any other failure means the
            # question was not answered, and treating that as absent drops the
            # session record while the branch survives — an orphaned branch nothing
            # points at, which is the same fail-open class as the tree check above
            # (basicly-jr0l.47).
            branch_removed = exists.returncode == 1
        if not branch_removed:
            detail = (deleted.stderr or deleted.stdout).strip()
            print(f"  note: branch {branch} not deleted ({detail})")

    # Keep the record when an unmerged branch survives, so `cleanup --force`
    # can still find and reclaim it once the worktree dir is already gone.
    if branch_removed:
        # Resolve the record against the primary checkout, not *repo_root*: the
        # worktree dir is gone by now, so a cwd pointing into it cannot answer.
        session_file(name, main).unlink(missing_ok=True)
        print(f"Cleaned up worktree {name!r} (worktree + branch + metadata).")
    else:
        print(
            f"Removed worktree {name!r}; kept branch {branch} and its record "
            "(unmerged — re-run with force to reclaim)."
        )
