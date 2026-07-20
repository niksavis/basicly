"""Merge orchestrator: parallel-build, serial-merge for harness worktrees.

Lands finished worktree branches back onto their base one at a time, in the
caller-supplied (topological) order, re-verifying after each. Conflicts are
detected non-destructively with ``git merge-tree`` before any working tree is
touched; the queue bounds residual conflicts with the rework policy (onb.3) and
then escalates to a human. Tracker state (``.beads/issues.jsonl``) is reconciled
with ``br sync --merge``, never by hand-editing conflict markers.

Merge runs from the base checkout — git refuses to update a branch that is
checked out in another worktree. Topological ordering is the caller's
responsibility (the decomposer/loop engine supplies it); the queue lands the
given order serially and stops at the first node that does not cleanly merge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import br, policy, run_record, verify
from .config import PolicyConfig, load_policy_config
from .worktree import current_branch, git, load_session

MERGE_GATE = "merge"


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a non-destructive ``git merge-tree`` conflict probe."""

    safe: bool
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class MergeResult:
    """Outcome of attempting to merge one worktree back to its base."""

    name: str
    # "merged" | "not-ready" | "rebase-conflicts" | "verify-failed"
    # | "merge-conflicts" | "merge-failed"
    status: str
    detail: str

    @property
    def merged(self) -> bool:
        """True when the worktree landed cleanly on its base."""
        return self.status == "merged"


def probe_merge(repo_root: Path, base: str, branch: str) -> ProbeResult:
    """Probe whether *branch* merges cleanly into *base* without touching a tree.

    ``git merge-tree --write-tree`` exits 0 when the merge applies cleanly and
    non-zero on conflict; nothing is written to any working tree or ref.
    """
    proc = git(
        ["merge-tree", "--write-tree", "--name-only", base, branch],
        cwd=repo_root,
        check=False,
    )
    if proc.returncode == 0:
        return ProbeResult(safe=True, conflicts=())
    # On conflict the first stdout line is the (partial) tree oid; the rest are paths.
    lines = proc.stdout.splitlines()
    conflicts = tuple(lines[1:] if len(lines) > 1 else lines)
    return ProbeResult(safe=False, conflicts=conflicts)


def _assert_base_ready(repo_root: Path, base: str) -> None:
    """Ensure the base checkout is on *base* with a clean tree before merging."""
    on = current_branch(repo_root)
    if on != base:
        raise SystemExit(
            f"merge must run from the base checkout with {base!r} checked out "
            f"(currently on {on!r}); git will not update a branch checked out elsewhere."
        )
    dirty = git(["status", "--porcelain"], cwd=repo_root).stdout.strip()
    if dirty:
        raise SystemExit(f"base checkout has uncommitted changes; commit or stash first:\n{dirty}")


def _worktree_land_readiness(
    worktree_path: Path, repo_root: Path, base: str, branch: str
) -> str | None:
    """Why *branch* is not ready to land, or None when it is.

    Landing rebases the branch, so the agent's work must be committed on it. A
    tree with uncommitted tracked changes makes ``git rebase`` abort with
    "unstaged changes", and a branch with no commits ahead of base has nothing
    to merge. Both are operator-fixable states, not conflicts or verification
    failures — the caller blocks with guidance rather than burning a rework
    attempt (basicly-4psl). Untracked files are ignored: ``git rebase`` does
    not abort on them.
    """
    dirty = git(["status", "--porcelain", "--untracked-files=no"], cwd=worktree_path).stdout.strip()
    if dirty:
        return (
            f"worktree has uncommitted changes; commit the work on {branch} before "
            f"landing (the loop does not auto-commit):\n{dirty}"
        )
    ahead = git(["rev-list", "--count", f"{base}..{branch}"], cwd=repo_root).stdout.strip()
    if ahead == "0":
        return (
            f"no committed work to land: {branch} has no commits ahead of {base} "
            "(commit the build's changes on the branch first)"
        )
    return None


def reconcile_beads(repo_root: Path) -> None:
    """Reconcile ``.beads/issues.jsonl`` via ``br sync --merge`` (no hand-editing)."""
    br.try_run_br(repo_root, ["sync", "--merge"])


def commit_tracker_state(
    repo_root: Path, bead: str, *, action: str = "sync tracker state for the harness loop"
) -> bool:
    """Commit the base checkout's dirt when it is tracker-only; False when it is not.

    The loop mutates the tracker from claim through gate recording while the
    agent builds (worktrees share it via br's ``redirect`` file), so `.beads/**`
    dirt in base is expected engine state, not the agent's business — roll it
    into one chore commit instead of blocking the advance on it. Any non-beads
    dirt still blocks: that is someone's uncommitted work.
    """
    lines = git(["status", "--porcelain"], cwd=repo_root).stdout.splitlines()
    paths = [line[3:] for line in lines if line.strip()]
    if not paths or not all(path.startswith(".beads/") for path in paths):
        return False
    br.try_run_br(repo_root, ["sync", "--flush-only"])
    git(["add", ".beads"], cwd=repo_root)
    git(["commit", "-m", f"chore(beads): {action} ({bead})"], cwd=repo_root)
    return True


def _known_bead_ids(repo_root: Path) -> set[str] | None:
    """Ids from ``.beads/issues.jsonl``, or None when no workspace exists."""
    issues = repo_root / ".beads" / "issues.jsonl"
    if not issues.exists():
        return None
    ids: set[str] = set()
    for raw_line in issues.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        issue_id = record.get("id")
        if isinstance(issue_id, str):
            ids.add(issue_id)
    return ids


def _merge_message(
    name: str, branch: str, base: str, bead: str, record: run_record.RunRecord | None = None
) -> str:
    """Build a Conventional-Commits merge message the commit-msg hook accepts.

    The subject is static (safe regardless of the worktree name); the specifics
    and the bead id live in the body so the beads hook is satisfied. When the
    dispatched runner is known (basicly-140a, from the run-*record*), it is
    stamped as ``Harness-Runner`` / ``Harness-Model`` git trailers in a final
    trailer paragraph, so history attributes the landed work to an agent instead
    of only the human git identity.
    """
    body = f"Integrate worktree {name} ({branch}) into {base}.\n\n{bead}"
    if record is not None and record.agent:
        trailers = [f"Harness-Runner: {record.agent}"]
        if record.model:
            trailers.append(f"Harness-Model: {record.model}")
        body += "\n\n" + "\n".join(trailers)
    return f"chore(worktree): merge a harness worktree back to its base\n\n{body}"


def merge_worktree(
    repo_root: Path, name: str, *, bead: str, verify_mode: str = "full"
) -> MergeResult:
    """Land worktree *name* onto its base: rebase, re-verify, probe, ``--no-ff`` merge.

    Runs from the base checkout. Returns a non-merged :class:`MergeResult` (never
    a partially applied merge) when the rebase conflicts, verification fails, or
    the conflict probe is not clean. Reconciles the tracker on success.
    """
    if not bead:
        raise SystemExit(
            "merge needs a bead id for the merge commit (the commit-msg hook requires one)"
        )
    known = _known_bead_ids(repo_root)
    if known is not None and bead not in known:
        raise SystemExit(
            f"unknown bead id {bead!r}: not in .beads/issues.jsonl — the commit-msg "
            "hook would reject the merge commit and strand the base mid-merge"
        )

    session = load_session(name, repo_root)
    if session is None:
        raise SystemExit(f"no worktree session named {name!r}")
    base, branch, worktree_path = session.base, session.branch, session.path

    # The agent's work must be committed on the branch before landing rebases
    # it. Check first, before mutating base: a dirty tree or an empty branch is
    # an operator-fixable state, not a conflict or a rework-worthy failure, and
    # bailing here avoids leaving a redundant tracker commit behind (basicly-4psl).
    not_ready = _worktree_land_readiness(worktree_path, repo_root, base, branch)
    if not_ready is not None:
        return MergeResult(name, "not-ready", not_ready)

    # Tracker-only dirt in base is the loop's own state (claim, checkpoints,
    # gate records) — roll it up before the clean-tree check instead of
    # bouncing the landing back to the agent.
    if current_branch(repo_root) == base:
        commit_tracker_state(repo_root, bead)
    _assert_base_ready(repo_root, base)

    # 1. Rebase onto the *current* base so serialized merges stay conflict-free.
    rebase = git(["rebase", base, branch], cwd=worktree_path, check=False)
    if rebase.returncode != 0:
        git(["rebase", "--abort"], cwd=worktree_path, check=False)
        return MergeResult(
            name, "rebase-conflicts", f"rebase of {branch} onto {base} hit conflicts"
        )

    # 2. Re-verify in the worktree after the rebase.
    report = verify.run_verify(worktree_path, verify_mode)
    if not report.passed:
        return MergeResult(
            name, "verify-failed", f"verify {verify_mode} failed: {', '.join(report.failures)}"
        )

    # 3. Non-destructive conflict probe before touching the base tree.
    probe = probe_merge(repo_root, base, branch)
    if not probe.safe:
        return MergeResult(name, "merge-conflicts", f"conflicts in: {', '.join(probe.conflicts)}")

    # 4. Local --no-ff merge into the base from the base checkout. A failure
    # (e.g. a commit-msg hook rejection) must not strand MERGE_HEAD. Attribute the
    # dispatched runner (basicly-140a) from the run-record, best-effort.
    record = run_record.latest_record(repo_root, bead)
    proc = git(
        ["merge", "--no-ff", branch, "-m", _merge_message(name, branch, base, bead, record)],
        cwd=repo_root,
        check=False,
    )
    if proc.returncode != 0:
        git(["merge", "--abort"], cwd=repo_root, check=False)
        return MergeResult(
            name,
            "merge-failed",
            f"git merge of {branch} exited {proc.returncode}; aborted, base left clean",
        )
    reconcile_beads(repo_root)
    head = git(["rev-parse", "--short", "HEAD"], cwd=repo_root).stdout.strip()
    return MergeResult(name, "merged", f"merged {branch} into {base} @ {head}")


@dataclass(frozen=True)
class QueueResult:
    """A queued merge's outcome plus the rework/escalation decision on failure."""

    result: MergeResult
    attempts: int = 0
    escalate: bool = False


def merge_queue(
    repo_root: Path,
    items: list[tuple[str, str]],
    *,
    config: PolicyConfig | None = None,
    verify_mode: str = "full",
) -> list[QueueResult]:
    """Land ``(name, bead)`` worktrees serially in the given (topological) order.

    Re-verifies after each merge. Stops at the first node that does not cleanly
    merge: records a rework attempt against that node's bead (policy, onb.3) and
    flags escalation once the rework cap is reached, so a human resolves the
    conflict before the queue is re-run. No conflict markers are hand-edited.
    """
    config = config or load_policy_config(repo_root)
    results: list[QueueResult] = []
    for name, bead in items:
        result = merge_worktree(repo_root, name, bead=bead, verify_mode=verify_mode)
        if result.merged:
            results.append(QueueResult(result))
            continue
        if result.status == "not-ready":
            # Operator-fixable (work not committed on the branch): stop the queue
            # so it is resolved, but do not spend a rework attempt on it.
            results.append(QueueResult(result))
            break
        attempts = policy.record_rework(repo_root, bead, MERGE_GATE)
        escalate = attempts >= config.max_rework
        results.append(QueueResult(result, attempts=attempts, escalate=escalate))
        break  # serial, human-gated: stop so the failure is resolved before continuing
    return results
