"""Merge orchestrator: parallel-build, serial-merge for harness worktrees.

Lands finished worktree branches back onto their base one at a time, in
dependency order, re-verifying after each. Conflicts are detected
non-destructively with ``git merge-tree`` before any working tree is touched;
the queue bounds residual conflicts with the rework policy (onb.3) and then
escalates to a human.

Merge runs from the base checkout — git refuses to update a branch that is
checked out in another worktree.

Queue v2 — consume-as-ready with bounce-back (factory design D5,
basicly-kjc5.10). The queue is the standing consumer the supervisor drives, so
one lane's state must not hold the others hostage:

- **Dependency order, computed here.** :func:`landing_order` sorts the queued
  items so a lane lands before the lanes that depend on it, read from ``br``
  rather than trusted from the caller.
- **Consume as ready.** A lane whose work is not committed yet is *deferred*,
  not fatal: it stays queued for a later pass while the lanes that are ready
  land now.
- **Conflicts bounce back to the owning lane.** A conflict means the
  decomposition's declared scopes missed a coupling — the graph was wrong, not
  the merge — so there is **never a merge-time AI resolution and never a
  hand-edited conflict marker**. The lane keeps its own commits, a rework
  attempt is recorded against its bead (bounded by ``[policy] max_rework``,
  escalating at the cap), the missed coupling is recorded as a non-gating
  ``related`` edge onto the lane that landed the conflicting paths so the graph
  learns without holding the bounce back (:func:`record_coupling`), and the
  lane's own agent re-applies its intent on the new base at the next dispatch.
- **A red suite or a rejected merge commit still stops the pass**: unlike a
  scope collision, that is a signal about the base itself, and stacking more
  landings on top of it only compounds the damage.

Tracker state (``.beads/issues.jsonl``) is reconciled with ``br sync --merge``,
never by hand-editing conflict markers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import br, policy, run_record, verify
from .config import PolicyConfig, load_policy_config
from .worktree import current_branch, git, load_session

MERGE_GATE = "merge"

# Landing failures that are scope collisions rather than gate or state problems.
# These bounce back to the owning lane (D5); everything else keeps its old stance.
CONFLICT_STATUSES = ("rebase-conflicts", "merge-conflicts")

# Path prefixes the harness rewrites on every landing (the tracker it reconciles
# with `br sync --merge`). A collision here is engine bookkeeping, never evidence
# of a coupling the decomposition missed.
ENGINE_PATHS = (".beads/",)

# Dependency type for a missed coupling. Deliberately not `blocks`: the edge
# teaches the next decomposition, and `br blocked` (so `supervise.ready_lanes`)
# must not hold the bounced lane behind it — see `record_coupling` (basicly-grrb).
COUPLING_DEP_TYPE = "related"

# Status for a landing whose verify gate failed and then passed unchanged on a
# re-run. Distinct from "verify-failed" because the two must be scored
# differently: a merit failure spends the lane's bounded rework budget, an
# unreliable gate carries no evidence against the work and must spend nothing
# (basicly-55yh). The lane that hit this in the kjc5.22 dogfood had committed
# three docs files and was escalated for an upstream tracker flake.
VERIFY_UNRELIABLE = "verify-unreliable"


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
    # | "verify-unreliable" | "merge-conflicts" | "merge-failed"
    status: str
    detail: str
    # Paths that collided, for a conflict status. Carried as data (not only in
    # the message) because the queue attributes the missed coupling from them
    # (D5); empty when git reported none.
    conflicts: tuple[str, ...] = ()

    @property
    def merged(self) -> bool:
        """True when the worktree landed cleanly on its base."""
        return self.status == "merged"

    @property
    def conflicted(self) -> bool:
        """True when the landing failed on a collision, not on a gate or a state."""
        return self.status in CONFLICT_STATUSES

    @property
    def unreliable(self) -> bool:
        """True when the gate failed and then passed unchanged on re-run.

        No evidence against the lane's work, so no caller may charge it a rework
        attempt (basicly-55yh).
        """
        return self.status == VERIFY_UNRELIABLE


def _verify_for_landing(name: str, worktree_path: Path, verify_mode: str) -> MergeResult | None:
    """Re-verify the rebased worktree: a blocking result, or None when it may land.

    Evidence before blame. When the gate fails, exactly the checks that failed are
    re-run in the same tree with nothing touched; a check that passes now did not
    fail on this lane's work, so scoring it as a merit failure would spend the
    lane's bounded rework budget on an unreliable gate (basicly-55yh). The re-run
    is paid for only on a failure, so a green landing costs nothing extra.
    """
    report = verify.run_verify(worktree_path, verify_mode)
    if report.passed:
        return None
    failures = ", ".join(report.failures)
    if verify.rerun_failures(report, worktree_path, verify_mode).passed:
        return MergeResult(
            name,
            VERIFY_UNRELIABLE,
            f"verify {verify_mode} failed on {failures} but passed unchanged on re-run",
        )
    return MergeResult(name, "verify-failed", f"verify {verify_mode} failed: {failures}")


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
    # br stamps each flushed record with the producing workspace's absolute path;
    # strip it before staging so the committed export never publishes a home
    # directory layout (basicly-vkh0.5). This is the engine's only tracker-commit
    # path, so scrubbing here covers every record br wrote since the last one.
    # The tracker-path-scan hook is the gate for whatever this misses.
    br.scrub_export(repo_root)
    git(["add", ".beads"], cwd=repo_root)
    git(["commit", "-m", f"chore(beads): {action} ({bead})"], cwd=repo_root)
    return True


def known_bead_ids(repo_root: Path) -> set[str] | None:
    """Ids from ``.beads/issues.jsonl``, or None when no workspace exists.

    Public because every path that composes a commit message owes the same check:
    the ``beads-commit-msg`` gate rejects an unknown id, and discovering that at
    commit time strands whatever the caller already wrote (merge mid-landing,
    release mid-bump).
    """
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
    known = known_bead_ids(repo_root)
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
        # Read the collided paths out of the stopped rebase before aborting: the
        # queue needs them to attribute the missed coupling (D5), and they are
        # gone once the rebase state is discarded.
        conflicts = unmerged_paths(worktree_path)
        git(["rebase", "--abort"], cwd=worktree_path, check=False)
        where = f" in: {', '.join(conflicts)}" if conflicts else ""
        return MergeResult(
            name,
            "rebase-conflicts",
            f"rebase of {branch} onto {base} hit conflicts{where}",
            conflicts=conflicts,
        )

    # 2. Re-verify in the worktree after the rebase.
    gate = _verify_for_landing(name, worktree_path, verify_mode)
    if gate is not None:
        return gate

    # 3. Non-destructive conflict probe before touching the base tree.
    probe = probe_merge(repo_root, base, branch)
    if not probe.safe:
        return MergeResult(
            name,
            "merge-conflicts",
            f"conflicts in: {', '.join(probe.conflicts)}",
            conflicts=probe.conflicts,
        )

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
    # Conflict handed back to the owning lane instead of stopping the pass (D5).
    bounced: bool = False
    # Beads whose landing this pass touched the conflicting paths, now recorded
    # as ``blocks`` dependency edges so the graph learns the missed coupling.
    couplings: tuple[str, ...] = ()

    @property
    def deferred(self) -> bool:
        """True when the lane was not landable yet and simply stays queued.

        Covers both no-charge outcomes: work not yet committed on the branch, and
        a gate that failed without reproducing (basicly-55yh). Neither faults the
        lane, so neither spends an attempt.
        """
        return self.result.status == "not-ready" or self.result.unreliable


def unmerged_paths(cwd: Path) -> tuple[str, ...]:
    """Paths git currently reports as unmerged in *cwd* (empty when none/unknown)."""
    proc = git(["diff", "--name-only", "--diff-filter=U"], cwd=cwd, check=False)
    if proc.returncode != 0:
        return ()
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


def blocking_dependencies(repo_root: Path, bead: str) -> frozenset[str]:
    """Ids *bead* is blocked by, per ``br``; empty when unreadable.

    ``br`` renders a dependency two ways — ``br show --json`` gives
    ``id``/``dependency_type`` while the ``create``/``dep add`` echo gives
    ``depends_on_id``/``type`` — so both spellings are read. (Trusting only the
    echo's spelling silently returned no dependencies at all, which degraded
    every landing order to the caller's.)

    Best-effort by design: ordering is an optimization over an already-correct
    serial landing, so an unreachable tracker degrades to the caller's order
    instead of refusing to land anything.
    """
    proc = br.try_run_br(repo_root, ["show", bead, "--json"])
    if proc is None or proc.returncode != 0:
        return frozenset()
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return frozenset()
    record = data[0] if isinstance(data, list) and data else data
    if not isinstance(record, dict):
        return frozenset()
    blocking: set[str] = set()
    for dep in record.get("dependencies") or []:
        if not isinstance(dep, dict):
            continue
        if (dep.get("dependency_type") or dep.get("type")) != "blocks":
            continue
        dep_id = dep.get("depends_on_id") or dep.get("id")
        if isinstance(dep_id, str) and dep_id:
            blocking.add(dep_id)
    return frozenset(blocking)


def landing_order(repo_root: Path, items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Sort queued items so a lane lands before the lanes that depend on it (D5).

    The dependency order is read from ``br`` rather than trusted from the caller,
    restricted to the queued beads — a dependency outside the queue is either
    already landed or not this pass's business. The sort is stable, so
    independent lanes keep the caller's (scheduler-rank) order, and an
    unresolvable remainder (a cycle, or an unreadable tracker) keeps it too
    rather than dropping a lane on the floor.
    """
    queued = {bead for _, bead in items}
    blocked_by = {bead: blocking_dependencies(repo_root, bead) & queued for _, bead in items}
    ordered: list[tuple[str, str]] = []
    landed: set[str] = set()
    remaining = list(items)
    while remaining:
        ready = [item for item in remaining if blocked_by[item[1]] <= landed]
        if not ready:
            ordered.extend(remaining)
            break
        for item in ready:
            ordered.append(item)
            landed.add(item[1])
            remaining.remove(item)
    return ordered


def missed_couplings(
    conflicts: tuple[str, ...], landed: list[tuple[str, tuple[str, ...]]]
) -> tuple[str, ...]:
    """Beads landed this pass whose changes touched *conflicts* (pure).

    The conflicting paths are the evidence of the coupling the decomposition
    missed: whoever landed them is who this lane should have been serialized
    after. Paths the engine owns are excluded — **every** landing rewrites the
    tracker, so counting it would blame every lane for a collision the engine
    reconciles itself. With no evidence left, nothing is attributed: a wrong
    dependency edge would teach the graph a coupling that does not exist.
    """
    collided = {path for path in conflicts if not _engine_owned(path)}
    if not collided:
        return ()
    return tuple(bead for bead, changed in landed if collided & set(changed))


def _engine_owned(path: str) -> bool:
    """True for a path the harness itself rewrites on every landing.

    Only a literal ``./`` prefix is stripped: a bare ``lstrip("./")`` eats the
    leading dot of a dot-directory and would never match ``.beads/`` again (the
    same trap the scope estimator documents).
    """
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return any(normalized.lstrip("/").startswith(prefix) for prefix in ENGINE_PATHS)


def record_coupling(repo_root: Path, bead: str, coupled_to: str) -> None:
    """Record that *bead* and *coupled_to* are coupled — what the graph missed (D5).

    The edge exists to *teach*, not to gate: its job is to make the coupling part
    of the graph so the next decomposition serializes the two instead of
    declaring them parallel-safe. So it is recorded as ``related``, not
    ``blocks``.

    This used to write ``blocks``, justified by "an edge onto an already-landed
    lane gates nothing now (that lane is done)". Under the supervisor that
    premise is false and the cost is severe (basicly-grrb): a lane the supervisor
    lands is routed ``merged`` and parks in verify awaiting a ship checkpoint, so
    the bead is still **open**, and ``br blocked`` — hence
    ``supervise.ready_lanes`` — drops the bounced lane. The one lane the bounce
    exists to send back to its agent was instead held behind a human approval on
    the lane it collided with, indefinitely at autonomy ceiling L0.

    ``related`` is invisible to ``br blocked`` and to
    :func:`blocking_dependencies`, so coupled lanes are no longer serialized
    within a landing pass; a genuine collision simply bounces again, bounded by
    the rework cap. That is the trade the design already wanted — the edge is for
    the *next* decomposition.

    Best-effort: a rejected edge (already present, or a cycle ``br`` refuses)
    must not turn a bounced lane into a crash. Note ``br`` refuses a duplicate
    rather than changing its type, so an edge some other path already recorded as
    ``blocks`` keeps that type.
    """
    br.try_run_br(repo_root, ["dep", "add", bead, coupled_to, "-t", COUPLING_DEP_TYPE])


def merge_queue(
    repo_root: Path,
    items: list[tuple[str, str]],
    *,
    config: PolicyConfig | None = None,
    verify_mode: str = "full",
) -> list[QueueResult]:
    """Land ``(name, bead)`` worktrees as they turn ready, in dependency order (D5).

    Re-verifies after each merge. A lane that is not committed yet is deferred
    and the pass continues; a lane that *conflicts* is bounced back to its owner
    (rework recorded, missed coupling written to the graph) and the pass
    continues with the lanes that can still land. A failed verify or a rejected
    merge commit still stops the pass — that is a signal about the base, not
    about one lane's scope.
    """
    config = config or load_policy_config(repo_root)
    results: list[QueueResult] = []
    landed: list[tuple[str, tuple[str, ...]]] = []
    for name, bead in landing_order(repo_root, items):
        before = head_sha(repo_root)
        result = merge_worktree(repo_root, name, bead=bead, verify_mode=verify_mode)
        if result.merged:
            landed.append((bead, changed_paths(repo_root, before)))
            results.append(QueueResult(result))
            continue
        if result.status == "not-ready":
            # Operator-fixable (work not committed on the branch), and nothing
            # this pass can resolve: leave it queued, spend no rework attempt on
            # it, and let the lanes behind it land.
            results.append(QueueResult(result))
            continue
        if result.unreliable:
            # The gate failed and then passed unchanged, so there is no evidence
            # against this lane: record the flake and leave it queued rather than
            # charging its bounded budget (basicly-55yh). Deliberately `continue`
            # where "verify-failed" breaks — a failure that does not reproduce is
            # not the signal about the base that stopping the pass exists for.
            policy.record_unreliable_gate(repo_root, bead, MERGE_GATE, result.detail)
            results.append(QueueResult(result))
            continue
        if result.conflicted:
            results.append(_bounce_back(repo_root, bead, result, landed, config))
            continue
        attempts = policy.record_rework(repo_root, bead, MERGE_GATE)
        escalate = attempts >= config.max_rework
        results.append(QueueResult(result, attempts=attempts, escalate=escalate))
        break  # a bad base, not a bad lane: stop before stacking more on it
    return results


def _bounce_back(
    repo_root: Path,
    bead: str,
    result: MergeResult,
    landed: list[tuple[str, tuple[str, ...]]],
    config: PolicyConfig,
) -> QueueResult:
    """Hand a conflicting lane back to its owner, recording what the graph missed.

    No merge-time resolution of any kind (D5): the base was left untouched by
    :func:`merge_worktree`, the lane keeps its own commits, and re-applying the
    intent on the new base is the lane agent's job at its next dispatch — bounded
    by the rework cap, escalating to a human at it.
    """
    couplings = missed_couplings(result.conflicts, landed)
    for culprit in couplings:
        record_coupling(repo_root, bead, culprit)
    attempts = policy.record_rework(repo_root, bead, MERGE_GATE)
    return QueueResult(
        result,
        attempts=attempts,
        escalate=attempts >= config.max_rework,
        bounced=True,
        couplings=couplings,
    )


def head_sha(repo_root: Path) -> str:
    """The base checkout's HEAD, or "" when it cannot be read (never fatal)."""
    proc = git(["rev-parse", "HEAD"], cwd=repo_root, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def changed_paths(repo_root: Path, before: str) -> tuple[str, ...]:
    """Paths a landing added to the base since *before* (empty when unknown).

    Public because coupling attribution needs it wherever landings happen: the
    supervisor lands lane by lane through the loop engine rather than through
    :func:`merge_queue`, and must attribute a conflict the same way (kjc5.20).
    """
    if not before:
        return ()
    proc = git(["diff", "--name-only", f"{before}..HEAD"], cwd=repo_root, check=False)
    if proc.returncode != 0:
        return ()
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())
