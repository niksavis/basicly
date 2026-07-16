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
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import policy, verify
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
    # "merged" | "rebase-conflicts" | "verify-failed" | "merge-conflicts" | "merge-failed"
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


def reconcile_beads(repo_root: Path) -> None:
    """Reconcile ``.beads/issues.jsonl`` via ``br sync --merge`` (no hand-editing)."""
    br = shutil.which("br")
    if not br:
        return
    subprocess.run(  # nosec B603
        [br, "sync", "--merge"], cwd=repo_root, capture_output=True, text=True, check=False
    )


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


def _merge_message(name: str, branch: str, base: str, bead: str) -> str:
    """Build a Conventional-Commits merge message the commit-msg hook accepts.

    The subject is static (safe regardless of the worktree name); the specifics
    and the trailing bead id live in the body so the beads hook is satisfied.
    """
    return (
        "chore(worktree): merge a harness worktree back to its base\n\n"
        f"Integrate worktree {name} ({branch}) into {base}.\n\n{bead}"
    )


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
    # (e.g. a commit-msg hook rejection) must not strand MERGE_HEAD.
    proc = git(
        ["merge", "--no-ff", branch, "-m", _merge_message(name, branch, base, bead)],
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
        attempts = policy.record_rework(repo_root, bead, MERGE_GATE)
        escalate = attempts >= config.max_rework
        results.append(QueueResult(result, attempts=attempts, escalate=escalate))
        break  # serial, human-gated: stop so the failure is resolved before continuing
    return results
