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
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import checkout, tracker_paths
from .checkout import (
    current_branch,
    git,
    git_common_dir,
    main_checkout,
    registered_worktrees,
    run,
    worktrees_root,
)
from .hooks import PRECOMMIT_CONFIG, hook_stages, install_hooks, load_hook_specs

BRANCH_PREFIX = "harness/"

# Re-exported under the name its callers hold it to: `loop` and `release` both
# refuse a transition when they are standing in a linked worktree, and both ask
# this module — which is also the object their tests patch.
is_linked_checkout = checkout.is_linked_checkout

# Heavy dependency dirs each worktree gets as its own standalone tree. They are
# freshly installed (not symlinked/copied from main), which keeps the worktree
# self-contained and makes teardown safe.
DEP_DIRS = (".venv", "node_modules")


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

    @property
    def stale(self) -> bool:
        """True when the checkout this record names is gone from disk.

        A record is not a worktree, and a stale one holds no concurrency slot: nothing
        occupies a checkout and nothing contends for a gate (basicly-gtoqu9).
        """
        return not self.path.exists()


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
    return [
        Session(**json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(sessions_dir(cwd).glob("*.json"))
    ]


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


def create(name: str, base: str | None = None, repo_root: Path | str | None = None) -> Session:
    """Create and provision a sibling worktree for *name*.

    Adds ``<repo>.worktrees/<name>`` on a new ``harness/<name>`` branch off
    *base* (default: the current branch), provisions its own dependency trees
    and git hooks, and records a session in the git common dir. The worktree's
    ledger is redirected at the base checkout's (a git-ignored ``redirect`` file), so
    every tracker read and write from the worktree — the engine and the commit-msg hook
    alike — reaches the one shared log: no divergent copy, nothing to reconcile at
    landing. The checked-out event log is deliberately left untouched; overwriting it
    with the base working-tree version would leave the worktree permanently dirty and
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

    # Tracker sharing first (before the slow dep install), because a lane that wrote to
    # its own checked-out ledger would lose every write at teardown (basicly-vkh0.8).
    notes: list[str] = []
    main = main_checkout(repo_root)
    if (main / tracker_paths.LEDGER_DIR_NAME).is_dir():
        target_ledger = worktree / tracker_paths.LEDGER_DIR_NAME
        target_ledger.mkdir(parents=True, exist_ok=True)
        # Machine-local and git-ignored — an absolute path here never reaches a commit.
        # It names the checkout rather than the directory, which is the one rule
        # `tracker_paths` states.
        (target_ledger / tracker_paths.REDIRECT_NAME).write_text(f"{main}\n", encoding="utf-8")
        notes.append(
            f"{(tracker_paths.LEDGER_DIR_NAME / tracker_paths.REDIRECT_NAME).as_posix()}: "
            f"tracker shared with the base checkout"
        )

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


def _resolve_worktree(
    name: str,
    main: Path,
    repo_root: Path | str | None = None,
    *,
    missing_ok: bool = False,
) -> tuple[Path, str | None] | None:
    """Return ``(worktree_path, branch)`` for *name*, or ``None`` when absent.

    Prefers the session record; falls back to ``git worktree list`` so a
    worktree with no session (e.g. one made by raw ``git worktree add``) can
    still be cleaned up safely. *name* matches a registered path or its
    directory basename.

    Raises when nothing matches, unless *missing_ok* — a caller that is tearing
    down asks for an end state, and an already-absent worktree is that state.
    """
    session = load_session(name, repo_root)
    if session is not None:
        return session.path, session.branch

    target = Path(name)
    for path, branch in registered_worktrees(main).items():
        if path == target or path.name == name:
            return path, branch
    if missing_ok:
        return None
    raise SystemExit(
        f"no worktree named {name!r}: no session record and no registered worktree "
        f"matches it. Run `git worktree list` to see them."
    )


def stale_sessions(cwd: Path | str | None = None) -> list[Session]:
    """Return sessions whose worktree directory no longer exists on disk.

    A stale record is left when a worktree is removed out-of-band; ``cleanup``
    still reclaims its branch and metadata.
    """
    return [session for session in list_sessions(cwd) if session.stale]


def unlanded_paths(main: Path, base: str, branch: str) -> tuple[str, ...] | None:
    """Paths *branch* changed whose content *base* does not hold, or None when unknown.

    ``git branch -d`` answers ancestry, and ancestry is not what makes a branch safe to
    discard. Two things separate them. A landing that squashed, cherry-picked or rewrote
    history above the fork point puts every line into base while leaving the ref
    unreachable. And ``git branch -d`` fails non-zero for reasons that are not about
    merging at all — measured 2026-08-20, a branch still checked out in a worktree gives
    ``cannot delete branch ... used by worktree``, and ``-D`` refuses it too, so the
    ``re-run with force`` the caller used to offer for *every* failure cannot even
    succeed there (basicly-8g719r).

    Only the paths the branch touched are compared. The whole-tree diff would name every
    path a sibling lane has landed since the fork, which is the same wrong-every-time
    answer pointing the other way — and being wrong toward "refuse" is what trains an
    operator to stop reading and pass ``--force``.

    Returns:
        The differing paths, sorted; ``()`` when base holds all of them; ``None`` when
        git could not answer. The caller fails closed on ``None``: a question nobody
        answered must not authorise deleting a branch.
    """
    fork = git(["merge-base", base, branch], cwd=main, check=False)
    if fork.returncode != 0 or not fork.stdout.strip():
        return None
    touched = git(["diff", "--name-only", fork.stdout.strip(), branch], cwd=main, check=False)
    against = git(["diff", "--name-only", branch, base], cwd=main, check=False)
    if touched.returncode != 0 or against.returncode != 0:
        return None
    changed = {line for line in touched.stdout.splitlines() if line.strip()}
    differs = {line for line in against.stdout.splitlines() if line.strip()}
    return tuple(sorted(changed & differs))


def _kept_for_content(main: Path, branch: str, base: str | None, detail: str) -> str:
    """Why a branch git refused to delete is kept, or ``""`` once content cleared it.

    Reached only after ``git branch -d`` has already said no, and it asks the question
    that one does not: does *base* hold what this branch changed. When it does, the ref is
    redundant rather than unmerged, and ``-D`` is the only flag that deletes a ref git
    calls unmerged — so the reclaim happens here rather than being handed to an operator
    as ``--force``, which is also what would have deleted the work in the case below.

    *detail* is git's own refusal, carried through so a kept branch says both what git
    said and what the content comparison found. Fails closed on every answer that is not
    "base holds all of it".
    """
    if base is None:
        return f"no session record names its base, so its content cannot be compared: {detail}"
    missing = unlanded_paths(main, base, branch)
    if missing is None:
        return f"its content could not be compared with {base}: {detail}"
    if missing:
        return (
            f"{base} does not hold {len(missing)} path(s) it changed, so this work is not "
            f"landed and force would discard it: {', '.join(missing)}"
        )
    purged = git(["branch", "-D", branch], cwd=main, check=False)
    if purged.returncode == 0:
        return ""
    return (
        f"{base} holds every path it changed, but git refused to delete it: "
        f"{(purged.stderr or purged.stdout).strip()}"
    )


def _reclaim_branch(main: Path, branch: str, base: str | None, *, force: bool) -> str:
    """Delete *branch*; return ``""`` once it is gone, else why it is being kept.

    Deciding on content rather than on ancestry, because a check that is wrong on the
    correct case trains its own bypass, and the bypass here is what deletes work. Every
    non-empty return names *what stands in the way*; the message this replaced named the
    remedy — ``--force`` — for whatever git had said, which on 2026-08-20 came within one
    command of discarding a commit base genuinely did not hold.

    The plain ``git branch -d`` stays as the fast path, so the ordinary merged branch
    costs exactly one git call and :func:`_kept_for_content` is only reached once git has
    already refused. ``force`` keeps its old meaning: ``-D`` up front, delete regardless,
    no content question asked.
    """
    deleted = git(["branch", "-D" if force else "-d", branch], cwd=main, check=False)
    if deleted.returncode == 0:
        return ""
    # A branch that is already gone (e.g. deleted by hand during a manual recovery) is
    # effectively removed — treat it so, or its session record is stranded and keeps
    # counting toward the concurrency cap. Only exit 1 means "the ref is absent": any
    # other failure means the question was not answered, and treating that as absent
    # drops the session record while the branch survives — an orphaned branch nothing
    # points at, which is the same fail-open class as the tree check (basicly-jr0l.47).
    absent = git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=main, check=False)
    if absent.returncode == 1:
        return ""
    detail = (deleted.stderr or deleted.stdout).strip()
    if force:
        return f"git refused to delete it: {detail}"
    return _kept_for_content(main, branch, base, detail)


def cap_refusal(concurrency: int, cwd: Path | str | None = None) -> str:
    """Why no further worktree may be provisioned, or ``""`` when a slot is free.

    Counts **checkouts, not records.** ``cleanup`` without ``--force`` keeps the record
    when the branch survives, and a plain ``git worktree remove`` tells the engine
    nothing, so either leaves a record whose checkout is gone holding a slot for good.

    Lives here rather than at its two callers because both had hand-written their own,
    and the wrong one's shape is the point: name only the cap and raising the cap is the
    cheapest reading, so a refusal names the stale records and what clears them
    (basicly-gtoqu9).
    """
    sessions = list_sessions(cwd)
    stale = [session for session in sessions if session.stale]
    live = len(sessions) - len(stale)
    if live < concurrency:
        return ""
    refusal = (
        f"worktree concurrency cap reached ({live}/{concurrency} live); clean up a "
        "worktree or raise [worktree].concurrency in basicly.toml"
    )
    if not stale:
        return refusal
    names = ", ".join(sorted(session.name for session in stale))
    return (
        f"{refusal}. {len(stale)} record(s) whose checkout is already gone hold no slot "
        f"but are still listed — reclaim them with `basicly worktree cleanup <name> "
        f"--force`: {names}"
    )


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

    The provisioned dep dirs and the tracker's worktree redirect are expected noise
    rather than work, and never hold a teardown. The redirect is named here as well as
    ignored, because a consumer's ``.gitignore`` is the consumer's file and a teardown
    may not depend on it (basicly-vkh0.42.4).
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
    expected_noise = (
        *DEP_DIRS,
        (tracker_paths.LEDGER_DIR_NAME / tracker_paths.REDIRECT_NAME).as_posix(),
    )
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


def cleanup(
    name: str,
    *,
    force: bool = False,
    repo_root: Path | str | None = None,
    missing_ok: bool = False,
) -> None:
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

    *missing_ok* returns quietly when nothing matches *name*, for a caller that
    wants the end state rather than the removal. A ship advance tears down and
    then closes; without this it died on the teardown of a worktree an earlier
    teardown had already removed, and left the shipped issue open at ship with a
    binding to a worktree and branch that no longer exist (basicly-e2mz.32).
    """
    main = main_checkout(repo_root)
    resolved = _resolve_worktree(name, main, repo_root, missing_ok=missing_ok)
    if resolved is None:
        return
    worktree, branch = resolved

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

    # Resolve the record against the primary checkout, not *repo_root*: the worktree dir
    # is gone by now, so a cwd pointing into it cannot answer.
    record = load_session(name, main)
    kept = (
        _reclaim_branch(main, branch, record.base if record else None, force=force)
        if branch
        else ""
    )

    # Keep the record when the branch survives, so `cleanup --force` can still find and
    # reclaim it once the worktree dir is already gone.
    if not kept:
        session_file(name, main).unlink(missing_ok=True)
        print(f"Cleaned up worktree {name!r} (worktree + branch + metadata).")
    else:
        print(f"Removed worktree {name!r}; kept branch {branch} and its record ({kept}).")
