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
  ``related`` edge onto the lane whose **declared scope** covers the conflicting
  paths so the graph learns without holding the bounce back
  (:func:`record_pass_couplings`), and the lane's own agent re-applies its intent
  on the new base at the next dispatch.
- **A generated artifact is rebuilt, not bounced** (basicly-lyro). The one exception
  to the rule above, and it is not a resolution of anything: a path keyed in
  ``[worktree.regenerate_commands]`` is a function of the tree, so when *every*
  unmerged path is in that table the rebase discards both sides, re-runs **that
  path's** rebuild, and continues. No lane is faulted and no rework is spent, because
  there is no coupling to learn — three lanes editing three different catalog sources
  all legitimately change the projection manifest. One undeclared path, or one the
  rebuild left a conflict marker in, and the whole rebase bounces untouched
  (basicly-3w51, :func:`basicly.rebase.rebuild_generated_conflicts`).
- **A replay never both drops content and reports success** (basicly-5vu4). Getting the
  branch onto base is :mod:`basicly.rebase`'s whole responsibility, including the two
  guards that make it honest: a branch carrying a merge commit is refused before the
  rebase runs, and a replay that loses tracked content is undone and refused after it.
- **The edge never depends on landing order** (D9, basicly-kjc5.32). Attribution
  runs once the pass is over, over every landing rather than the prefix that
  happened to precede a bounce, and reads declared scopes rather than what each
  landing changed — so the same plan teaches the graph the same edge whichever
  lane lands first.
- **A red suite or a rejected merge commit still stops the pass**: unlike a
  scope collision, that is a signal about the base itself, and stacking more
  landings on top of it only compounds the damage.

Tracker state (``.beads/issues.jsonl``) is reconciled with ``br sync --merge``,
never by hand-editing conflict markers.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from . import br, decompose, owned_store, policy, rebase, run_record, verify
from .config import PolicyConfig, load_policy_config
from .worktree import Session, current_branch, git, load_session

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

# Status for a landing whose verify gate failed on a *tracker-wide* assertion that
# another lane's finishing record invalidated. Distinct from "verify-failed" for the
# same reason VERIFY_UNRELIABLE is, and from VERIFY_UNRELIABLE because the two are
# cleared by opposite evidence: a flake stops reproducing, while a record in the
# shared tracker is durable and reproduces forever. Every lane in a supervised pass
# shares one `.beads` through the redirect, so one lane's declaration failed this
# gate inside two siblings' landings and charged each of them a rework attempt for a
# defect in neither diff (basicly-qorx). Nothing here faults the lane, so it spends
# no rework; the culprits are carried on the result for the caller to attribute.
VERIFY_FOREIGN = "verify-foreign"

# Status for a lane whose branch moved after the queue was formed. The queue ranked
# it, and every earlier check read it, in a state that no longer exists — landing it
# would merge commits this pass never looked at (basicly-jr0l.46). Like "not-ready"
# it is a state and not a merit failure: the lane keeps its rework budget, and the
# next pass re-reads the branch and lands it normally.
STALE_BRANCH = "stale-branch"

# Status for a merge whose own return code claimed success but whose result could
# not be proved — after merging, the lane's head is still not reachable from the
# base ref. This repo has twice recorded a bead closed with its code stranded
# unmerged, and both times the only check that noticed was the ship gate, after the
# fact (basicly-jr0l.46). Nothing here is evidence against the lane's work, so it
# spends no rework, but it stops the pass: the base is in a state no further landing
# should be stacked on.
MERGE_UNPROVEN = "merge-unproven"

# Status for a lane whose branch is already an ancestor of base with no verify gate
# recorded — the half-landed state a crash between the merge and the gate record
# leaves behind (basicly-jr0l.50). It is a *success* the engine failed to finish
# writing down, not a failure: `rev-list base..branch == 0` used to read it as "no
# committed work to land", which both misled the operator and charged a supervised
# lane a rework attempt for a landing that worked. The landing resumes at the gate.
# Ancestry alone does not earn this status: a branch nobody committed to is an
# ancestor of base too, and reading that as a landing shipped a bead with an empty
# diff (basicly-tcmy.29). The branch must also have grown a commit since it was cut.
ALREADY_LANDED = "already-landed"

# Statuses a landing returns *before* its post-rebase verify gate runs: the three
# pre-merge states, and a rebase that stopped. Enumerated here, beside the order it
# describes, so a caller carrying a one-shot gate override does not have to re-derive
# from the outside whether the gate was ever reached (basicly-tcmy.6).
PRE_GATE_STATUSES = (
    "not-ready",
    STALE_BRANCH,
    ALREADY_LANDED,
    "rebase-conflicts",
    # Both replay-integrity refusals stop before the gate too, and neither belongs in
    # CONFLICT_STATUSES: a branch git would mangle is a branch-shape defect, not a scope
    # collision, so routing it there would record a `related` edge against a lane that
    # collided with nobody (basicly-5vu4).
    rebase.MERGE_COMMIT_ON_BRANCH,
    rebase.REPLAY_DROPPED_PATHS,
)


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
    # | "verify-unreliable" | "verify-foreign" | "merge-conflicts" | "merge-failed"
    # | "stale-branch" | "merge-unproven" | "already-landed"
    status: str
    detail: str
    # Paths that collided, for a conflict status. Carried as data (not only in
    # the message) because the queue attributes the missed coupling from them
    # (D5); empty when git reported none.
    conflicts: tuple[str, ...] = ()
    # Lanes whose records invalidated a tracker-wide gate, for VERIFY_FOREIGN.
    # Carried as data for the same reason *conflicts* is: the caller records the
    # attribution against them, and must not have to parse it back out of the
    # message (basicly-qorx).
    culprits: tuple[str, ...] = ()

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

    @property
    def foreign(self) -> bool:
        """True when the gate failed on another lane's record in the shared tracker.

        No evidence against the lane's work either, so no caller may charge it a
        rework attempt (basicly-qorx) — but unlike :attr:`unreliable` it will not
        clear on a re-attempt, so a caller must attribute it to :attr:`culprits`
        rather than merely defer.
        """
        return self.status == VERIFY_FOREIGN

    @property
    def reached_gate(self) -> bool:
        """True when the landing got as far as running (or skipping) its verify gate.

        What a caller holding a one-shot gate override asks before spending it: a lane
        that was not committed, whose branch moved, that had already landed, or whose
        rebase stopped never reached the gate, so nothing was overridden and the
        operator's single authorisation must survive (basicly-tcmy.6).
        """
        return self.status not in PRE_GATE_STATUSES


def _shared_tracker_failure(
    repo_root: Path, report: verify.VerifyReport, bead: str
) -> policy.SharedGateFailure | None:
    """The tracker-wide failure *every* failing check in *report* is explained by.

    Same bound as :func:`verify.dependency_defect`, and for the same reason: a run
    that mixes a gate another lane's record invalidated with a real failure is a real
    failure, so one unexplained check ends the forgiveness. Culprits and reasons are
    merged across the checks so a report faulting two lanes attributes to both.

    Every culprit must be a bead ``.beads/issues.jsonl`` actually holds, and that
    check is not ceremony: pytest elides the middle of a long assertion repr, so a
    truncated line can leave a *partial* id ("basicly-tcm") beside text the register
    matches. Attributing to that would forgive a real failure and blame nothing, so an
    unreadable workspace or an unknown id means the lane keeps the failure — the safe
    direction, since the forgiveness is what needs proving.
    """
    failures = [r for r in report.results if r.status == "fail"]
    if not failures:
        return None
    known = known_bead_ids(repo_root)
    if known is None:
        return None
    culprits: list[str] = []
    reasons: list[str] = []
    for result in failures:
        found = policy.shared_tracker_gate_failure(result.output or "", bead)
        if found is None or not set(found.culprits) <= known:
            return None
        culprits += [one for one in found.culprits if one not in culprits]
        if found.reason not in reasons:
            reasons.append(found.reason)
    return policy.SharedGateFailure(tuple(culprits), "; ".join(reasons))


def _verify_for_landing(
    repo_root: Path, name: str, worktree_path: Path, verify_mode: str, bead: str
) -> MergeResult | None:
    """Re-verify the rebased worktree: a blocking result, or None when it may land.

    Evidence before blame. When the gate fails, exactly the checks that failed are
    re-run in the same tree with nothing touched; a check that passes now did not
    fail on this lane's work, so scoring it as a merit failure would spend the
    lane's bounded rework budget on an unreliable gate (basicly-55yh). The re-run
    is paid for only on a failure, so a green landing costs nothing extra.

    Two ways to clear the lane, because the re-run test alone is not enough
    (basicly-kjc5.56). A failure that does not reproduce is unreliable. So is one
    that *does* reproduce but carries a signature only a dependency can emit: a
    backwards clock step persists for a window, so re-running inside that window
    reproduces a failure the work under test could not have caused. The re-run
    captures its output for that second test; no other run's output is diverted.

    A third way, on the same captured output and last because it is the narrowest: a
    failure that reproduces, is ours rather than a dependency's, and asserts over the
    whole shared tracker on a record belonging to some *other* lane
    (:func:`_shared_tracker_failure`). That is not the lane's work failing either,
    and it is why *bead* is required here — the lane's own id is what separates "your
    declaration broke this" from "a sibling's did" (basicly-qorx).
    """
    report = verify.run_verify(worktree_path, verify_mode)
    if report.passed:
        return None
    failures = ", ".join(report.failures)
    rerun = verify.rerun_failures(report, worktree_path, verify_mode, capture=True)
    if rerun.passed:
        return MergeResult(
            name,
            VERIFY_UNRELIABLE,
            f"verify {verify_mode} failed on {failures} but passed unchanged on re-run",
        )
    if (defect := verify.dependency_defect(rerun)) is not None:
        return MergeResult(
            name,
            VERIFY_UNRELIABLE,
            f"verify {verify_mode} failed on {failures} — known dependency defect, {defect}",
        )
    if (shared := _shared_tracker_failure(repo_root, rerun, bead)) is not None:
        return MergeResult(
            name,
            VERIFY_FOREIGN,
            f"verify {verify_mode} failed on {failures} — invalidated in the shared "
            f"tracker by {', '.join(shared.culprits)}, not by this lane's diff: "
            f"{shared.reason}",
            culprits=shared.culprits,
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


def _session_branch_head(repo_root: Path, name: str) -> str | None:
    """The head of worktree *name*'s branch, or None when it cannot be resolved.

    None means "do not check staleness for this lane". Unreadable is not treated as
    moved: a missing session is already reported by :func:`merge_worktree` with a
    clear error, and inventing a mismatch here would replace it with a confusing
    refusal. Skipping the check costs only the opportunistic pre-merge refusal — the
    post-merge proof in :func:`_merge_and_prove` is the guard that cannot be skipped.
    """
    try:
        session = load_session(name, repo_root)
    except OSError, RuntimeError:
        # worktree.run wraps any git failure in RuntimeError; outside a git checkout
        # there is no branch to have moved.
        return None
    return branch_head(repo_root, session.branch) if session is not None else None


def branch_head(repo_root: Path, branch: str) -> str | None:
    """The commit *branch* points at, or None when the ref does not resolve.

    Kept separate from :func:`head_sha` because this must not raise on a missing
    ref: a lane whose branch is gone is a state the caller decides about, and a
    ``None`` that reads as "unknown" is safer than an exception mid-landing.
    """
    proc = git(["rev-parse", "--verify", f"refs/heads/{branch}"], cwd=repo_root, check=False)
    head = proc.stdout.strip()
    return head if proc.returncode == 0 and head else None


def is_ancestor(repo_root: Path, commit: str, target: str) -> bool:
    """True when *commit* is reachable from *target* — the proof a merge landed.

    ``git merge-base --is-ancestor`` exits 0 for an ancestor and 1 otherwise, so a
    git failure of any other kind reads as *not* proved. That direction is
    deliberate: this answers "may I claim this landed", and an unanswerable question
    must never be read as yes (basicly-jr0l.46).
    """
    return (
        git(["merge-base", "--is-ancestor", commit, target], cwd=repo_root, check=False).returncode
        == 0
    )


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


def _branch_has_own_commits(repo_root: Path, session: Session) -> bool | None:
    """Whether *session*'s branch grew a commit since it was cut, or None when unprovable.

    The session record keeps the commit the branch was branched from
    (``base_head``), which is the only thing that separates a lane that did work
    from one that did none — ancestry cannot, because both are ancestors of base
    (basicly-tcmy.29). A branch that has never been committed to still points at
    its creation commit; one that landed and lost only its gate record does not.

    ``None`` means the question could not be answered — no recorded creation
    commit, or git could not resolve it (a worktree provisioned before the field
    existed, a rewritten base). The caller fails closed on it.
    """
    if not session.base_head:
        return None
    proc = git(
        ["rev-list", "--count", f"{session.base_head}..{session.branch}"],
        cwd=repo_root,
        check=False,
    )
    count = proc.stdout.strip()
    if proc.returncode != 0 or not count.isdigit():
        return None
    return count != "0"


def _worktree_land_readiness(repo_root: Path, session: Session) -> MergeResult | None:
    """The landing outcome the lane's current state forces, or None when it may land.

    Landing rebases the branch, so the agent's work must be committed on it. A
    tree with uncommitted tracked changes makes ``git rebase`` abort with
    "unstaged changes". That is an operator-fixable state, not a conflict or a
    verification failure — the caller blocks with guidance rather than burning a
    rework attempt (basicly-4psl). Untracked files are ignored: ``git rebase`` does
    not abort on them.

    A branch with no commits ahead of base is *three* different situations, and
    ancestry alone only separates one of them. If the branch is not an ancestor,
    there is genuinely nothing to merge. If it is, the merge may have happened with
    only the gate record missing — a crash between the two leaves exactly that
    state, so the landing forward-recovers (``ALREADY_LANDED``) instead of reporting
    "no committed work to land" and charging the lane for it (basicly-jr0l.50).

    But a branch nobody ever committed to is an ancestor of base too, trivially, so
    that recovery swallowed it: a lane that did no work reported ``[merged]``,
    recorded a passing verify gate against a tree identical to base, and shipped and
    closed its bead with an empty diff (basicly-tcmy.29). :func:`_branch_has_own_commits`
    is what tells the two apart, and an unanswerable "did this branch ever receive a
    commit" blocks rather than recovering — a spurious block costs one command,
    while an empty landing is unrecoverable once the bead has closed.
    """
    name, base, branch = session.name, session.base, session.branch
    dirty = git(["status", "--porcelain", "--untracked-files=no"], cwd=session.path).stdout.strip()
    if dirty:
        return MergeResult(
            name,
            "not-ready",
            f"worktree has uncommitted changes; commit the work on {branch} before "
            f"landing (the loop does not auto-commit):\n{dirty}",
        )
    ahead = git(["rev-list", "--count", f"{base}..{branch}"], cwd=repo_root).stdout.strip()
    if ahead != "0":
        return None
    if is_ancestor(repo_root, branch, base):
        has_own = _branch_has_own_commits(repo_root, session)
        if has_own is None:
            return MergeResult(
                name,
                "not-ready",
                f"cannot prove {branch} ever received a commit: its recorded creation "
                f"commit {session.base_head or '(unrecorded)'} is unreadable, so the "
                "landing blocks instead of recording a gate for work that may not "
                "exist (commit the build's changes on the branch, or re-provision)",
            )
        if has_own:
            return MergeResult(
                name,
                ALREADY_LANDED,
                f"{branch} is already an ancestor of {base}: the merge landed and only the "
                "gate record is missing, so the landing resumes at the gate",
            )
    return MergeResult(
        name,
        "not-ready",
        f"no committed work to land: {branch} has no commits ahead of {base} "
        "(commit the build's changes on the branch first)",
    )


def reconcile_beads(repo_root: Path) -> str:
    """Reconcile ``.beads/issues.jsonl`` via ``br sync --merge`` (no hand-editing).

    Returns br's refusal, or empty on success. A warning rather than a raise because
    the git merge has already landed by the time this runs, and unwinding it over an
    unreconciled tracker would cost more than the operator re-running the sync.
    """
    proc = br.try_run_br(repo_root, ["sync", "--merge"])
    if proc is None or proc.returncode == 0:
        return ""
    return (
        f"WARNING tracker NOT reconciled — br sync --merge exited {proc.returncode}: "
        f"{(proc.stderr or proc.stdout or '').strip()}"
    )


# The trees the engine writes while a lane builds, so their dirt in base is the loop's
# own state. The ledger joined `.beads` at dual write (basicly-vkh0.25): a lane's claim,
# gate reports and comments append there, so it is dirty exactly when the merge runs.
# Two named trees, never "commit whatever is dirty", or `foreign_dirt` protects nothing.
ENGINE_TRACKER_PATHS = (".beads", owned_store.LEDGER_DIR.as_posix())


def _under(path: str, tree: str) -> bool:
    return path.startswith(f"{tree}/")


def is_engine_tracker_path(path: str) -> bool:
    """Whether a git-status path is one the loop commits itself.

    Public so `loop preflight` can ask it of a status it already holds: a second copy
    there is how the landing and the preflight came to disagree.
    """
    return any(_under(path, tree) for tree in ENGINE_TRACKER_PATHS)


def foreign_dirt(repo_root: Path) -> tuple[str, ...]:
    """Dirty paths in *repo_root* that are not the loop's own tracker state.

    Anything outside :data:`ENGINE_TRACKER_PATHS` is somebody's uncommitted work, which
    is why :func:`commit_tracker_state` declines to sweep it into a ``chore(beads)``
    commit. Public because a caller that was declined has to be able to *say* what
    blocked it: reporting "the tracker state was not committed" without naming the
    paths leaves an operator to rediscover them (basicly-f7li).
    """
    lines = git(["status", "--porcelain"], cwd=repo_root).stdout.splitlines()
    paths = [line[3:] for line in lines if line.strip()]
    return tuple(path for path in paths if not is_engine_tracker_path(path))


def skipped_tracker_commit_warning(repo_root: Path) -> str:
    """The warning for an advance whose tracker-state commit was declined.

    Empty when nothing foreign is dirty — that case is simply "there was nothing
    to commit", which is not worth a word. Otherwise it names the blocking paths
    and the recovery, because the failure mode this exists to stop was silent: the
    advance reported a clean ship, and the operator pushed the code without the
    tracker state and learned about it later as unexplained dirt.
    """
    foreign = foreign_dirt(repo_root)
    if not foreign:
        return ""
    return (
        "WARNING tracker state NOT committed — these paths are dirty in the base "
        f"checkout and are not the loop's to commit: {', '.join(foreign)}; stash or "
        "commit them and re-run the advance to publish the tracker state"
    )


def commit_tracker_state(
    repo_root: Path, bead: str, *, action: str = "sync tracker state for the harness loop"
) -> bool:
    """Commit the base checkout's dirt when it is tracker-only; False when it is not.

    The loop mutates the tracker from claim through gate recording while the
    agent builds (worktrees share it via br's ``redirect`` file), so dirt under
    :data:`ENGINE_TRACKER_PATHS` is expected engine state, not the agent's business —
    roll it into one chore commit instead of blocking the advance on it. Anything
    else still blocks: that is someone's uncommitted work.

    A caller that gets ``False`` owes the operator an explanation — see
    :func:`skipped_tracker_commit_warning`.

    Raises:
        RuntimeError: br declined the flush, so the export on disk is not the state
            br holds and nothing is committed.
    """
    lines = git(["status", "--porcelain"], cwd=repo_root).stdout.splitlines()
    paths = [line[3:] for line in lines if line.strip()]
    if not paths or not all(is_engine_tracker_path(path) for path in paths):
        return False
    proc = br.try_run_br(repo_root, ["sync", "--flush-only"])
    if proc is not None and proc.returncode != 0:
        # br's sync guards refuse rather than write (a conflict-marked export exits 7,
        # measured through this call against the pinned 0.2.16); the three lines below
        # would commit the export br declined to produce, as a chore(beads) landing
        # indistinguishable from a real one (basicly-ho3t).
        raise RuntimeError(
            f"br sync --flush-only exited {proc.returncode}; tracker state NOT committed "
            f"— the export on disk is not what br would write: "
            f"{(proc.stderr or proc.stdout or '').strip()}"
        )
    # br stamps each flushed record with the producing workspace's absolute path;
    # strip it before staging so the committed export never publishes a home
    # directory layout (basicly-vkh0.5). This is the engine's only tracker-commit
    # path, so scrubbing here covers every record br wrote since the last one.
    # The tracker-path-scan hook is the gate for whatever this misses.
    br.scrub_export(repo_root)
    # The ledger is committed by the same commit and carries the same leak one field
    # over — br writes `created_by` on every record and the import copies it (r166).
    br.scrub_ledger(repo_root)
    # Staged per tree that actually has dirt, not per tree that could: a repo on
    # `external` has no ledger, and `git add` on an absent pathspec exits 128 rather
    # than skipping it. Derived from *paths* rather than from disk so the decision stays
    # a function of what git reported.
    dirty = [tree for tree in ENGINE_TRACKER_PATHS if any(_under(path, tree) for path in paths)]
    git(["add", *dirty], cwd=repo_root)
    git(["commit", "-m", f"chore(beads): {action} ({bead})"], cwd=repo_root)
    return True


def known_bead_ids(repo_root: Path) -> set[str] | None:
    """Ids from ``.beads/issues.jsonl``, or None when no workspace exists.

    Public because every path that composes a commit message owes the same check:
    the ``beads-commit-msg`` gate rejects an unknown id, and discovering that at
    commit time strands whatever the caller already wrote (merge mid-landing,
    release mid-bump), through :func:`basicly.br.beads_dir` so a redirect cannot
    split it (basicly-tcmy.19).
    """
    issues = br.beads_dir(repo_root) / "issues.jsonl"
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
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            ids.add(record["id"])
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


def _pre_merge_state(
    repo_root: Path, session: Session, expected_head: str | None
) -> MergeResult | None:
    """The outcome the lane's current state forces before base is touched, or None.

    Nothing decided here is a merit failure, so the queue spends none of the lane's
    rework budget on any of it: two are operator-fixable states that stay queued, and
    one (``ALREADY_LANDED``) is a landing that already succeeded and only needs its
    gate written down.
    """
    name, branch = session.name, session.branch
    # The agent's work must be committed on the branch before landing rebases it. A
    # dirty tree or an empty branch is not a conflict or a verification failure, and
    # bailing before base is mutated avoids leaving a redundant tracker commit
    # behind (basicly-4psl). This also recognises the half-landed branch, and it must
    # stay ahead of the staleness check below: a branch that already merged has
    # necessarily "moved" relative to whatever head the queue recorded, and refusing
    # it as stale would re-strand the very state this recovers (basicly-jr0l.50).
    forced = _worktree_land_readiness(repo_root, session)
    if forced is not None:
        return forced

    # A lane that grew a commit after the queue was formed was ranked, ordered, and
    # (for the lanes behind it) probed against a state that no longer exists. The
    # next pass re-reads the branch and lands it normally (basicly-jr0l.46).
    if expected_head is None:
        return None
    moved_to = branch_head(repo_root, branch)
    if moved_to == expected_head:
        return None
    found = moved_to[:12] if moved_to else "(missing)"
    return MergeResult(
        name,
        STALE_BRANCH,
        f"{branch} moved since it was queued: expected {expected_head[:12]}, found "
        f"{found} — requeue so the landing reads the branch it verified",
    )


def _merge_and_prove(
    repo_root: Path, name: str, *, base: str, branch: str, bead: str
) -> MergeResult:
    """Merge *branch* into *base*, then prove it landed before claiming it did.

    ``git merge`` exiting 0 is the merge's own account of itself. The only thing
    that establishes the work landed is re-resolving the target ref afterwards and
    finding the lane's head reachable from it, so ``merged`` is unreachable without
    that proof (basicly-jr0l.46). Twice in this repo a bead closed with its code
    stranded on a harness branch, and both times the sole check that noticed was the
    ship gate, long after the fact.
    """
    # A failure (e.g. a commit-msg hook rejection) must not strand MERGE_HEAD.
    # Attribute the dispatched runner (basicly-140a) from the run-record, best-effort.
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
    landed_head = branch_head(repo_root, branch)
    if landed_head is None or not is_ancestor(repo_root, landed_head, base):
        where = landed_head[:12] if landed_head else "(unresolvable)"
        return MergeResult(
            name,
            MERGE_UNPROVEN,
            f"git merge of {branch} reported success but {where} is not reachable from "
            f"{base}: the work is not landed — inspect base before landing anything else",
        )
    unreconciled = reconcile_beads(repo_root)
    head = git(["rev-parse", "--short", "HEAD"], cwd=repo_root).stdout.strip()
    detail = f"merged {branch} into {base} @ {head}"
    return MergeResult(name, "merged", f"{detail}; {unreconciled}" if unreconciled else detail)


def merge_worktree(  # noqa: PLR0913 — one keyword per independent landing input
    repo_root: Path,
    name: str,
    *,
    bead: str,
    verify_mode: str = "full",
    expected_head: str | None = None,
    override_gate: bool = False,
) -> MergeResult:
    """Land worktree *name* onto its base: rebase, re-verify, probe, ``--no-ff`` merge.

    Runs from the base checkout. Returns a non-merged :class:`MergeResult` (never
    a partially applied merge) when the rebase conflicts, verification fails, or
    the conflict probe is not clean. Reconciles the tracker on success.

    *expected_head* is the branch head recorded when the lane entered the queue.
    When it is supplied and the branch has moved since, the landing is refused
    (``STALE_BRANCH``) instead of merging commits this pass never examined.

    *override_gate* skips the post-rebase re-verify entirely. Only the loop sets it,
    and only after spending the one-shot override an operator's answered ``land
    anyway`` authorises (:func:`policy.spend_gate_override`, basicly-tcmy.6):
    re-running the gate is precisely the thing that answer rules out, so honouring
    the answer by running it again would carry the remedy out in name only. The
    caller keeps the whole burden of proving the authorisation — nothing here reads
    the tracker to second-guess it — and the landing's own verify gate, which the
    escalation does not ask about, still runs afterwards in the base checkout.

    A ``merged`` status is unreachable without proving it: after the merge, the
    lane's head must be reachable from the base ref, or the result is
    ``MERGE_UNPROVEN``. Trusting ``git merge``'s exit code alone is what let two
    beads close with their code stranded on a harness branch (basicly-jr0l.46).
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

    # Both pre-merge refusals are states, not merit failures, and both are checked
    # before base is touched.
    blocked = _pre_merge_state(repo_root, session, expected_head)
    if blocked is not None:
        return blocked

    # Tracker-only dirt in base is the loop's own state (claim, checkpoints,
    # gate records) — roll it up before the clean-tree check instead of
    # bouncing the landing back to the agent.
    if current_branch(repo_root) == base:
        commit_tracker_state(repo_root, bead)
    _assert_base_ready(repo_root, base)

    # 1. Replay onto the *current* base so serialized merges stay conflict-free.
    replayed = rebase.replay(repo_root, worktree_path, base, branch)
    if not replayed.ok:
        return MergeResult(name, replayed.status, replayed.detail, conflicts=replayed.conflicts)
    regenerated = replayed.regenerated + rebase.refresh_generated(repo_root, worktree_path, bead)

    # 2. Re-verify in the worktree after the rebase.
    if not override_gate:
        gate = _verify_for_landing(repo_root, name, worktree_path, verify_mode, bead)
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

    # 4/5. Merge, then prove the merge — a "merged" status is unreachable without it.
    landed = _merge_and_prove(repo_root, name, base=base, branch=branch, bead=bead)
    if landed.merged and regenerated:
        # Say it out loud wherever the landing is reported. This is the one place the
        # queue resolves a conflict rather than bouncing it, and a resolution nobody
        # is told about is indistinguishable from a rebase that never conflicted.
        return replace(landed, detail=f"{landed.detail} (regenerated {', '.join(regenerated)})")
    return landed


@dataclass(frozen=True)
class QueueResult:
    """A queued merge's outcome plus the rework/escalation decision on failure."""

    result: MergeResult
    attempts: int = 0
    escalate: bool = False
    # Conflict handed back to the owning lane instead of stopping the pass (D5).
    bounced: bool = False
    # Beads that landed this pass whose declared scope covers the conflicting
    # paths, recorded as `related` edges so the graph learns the missed coupling.
    # Filled after the pass, not at the bounce (`_attribute_pass`, D9).
    couplings: tuple[str, ...] = ()

    @property
    def deferred(self) -> bool:
        """True when the lane was not landable yet and simply stays queued.

        Covers every no-charge outcome: work not yet committed on the branch, a
        branch that moved after the queue was formed (basicly-jr0l.46), a gate that
        failed without reproducing (basicly-55yh), and a tracker-wide gate another
        lane's record invalidated (basicly-qorx). None of the four faults the lane,
        so none spends an attempt.
        """
        return (
            self.result.status in ("not-ready", STALE_BRANCH)
            or self.result.unreliable
            or self.result.foreign
        )


def blocking_dependencies(repo_root: Path, bead: str) -> frozenset[str]:
    """Ids *bead* is blocked by, per ``br``; empty when unreadable.

    Both of br's dependency spellings are read, via :func:`basicly.br.dependency_edge`
    — trusting only the echo's spelling silently returned no dependencies at all,
    which degraded every landing order to the caller's (basicly-kjc5.10, carried as
    requirement R2 on the replacement).

    Best-effort by design: ordering is an optimization over an already-correct
    serial landing, so an unreachable tracker degrades to the caller's order
    instead of refusing to land anything.
    """
    record = br.read_record(repo_root, bead)
    if record is None:
        return frozenset()
    blocking: set[str] = set()
    for dep in record.get("dependencies") or []:
        edge = br.dependency_edge(dep)
        if edge is not None and edge[1] == "blocks":
            blocking.add(edge[0])
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
    missed: whoever landed them is who this lane collided with. Paths the engine
    owns are excluded — **every** landing rewrites the tracker, so counting it
    would blame every lane for a collision the engine reconciles itself. With no
    evidence left, nothing is attributed.

    This is the *within-pass* answer, and it is order-sensitive by construction —
    it names whoever had already landed. So it is only for outcomes that die with
    the pass: D6's pre-empt (``supervise._invalidated_by``) reads it to tell one
    lane's next prompt which landing broke its merge. The dependency edge, which
    outlives the pass, must not be attributed this way — see
    :func:`coupled_lanes`.
    """
    collided = {path for path in conflicts if not _engine_owned(path)}
    if not collided:
        return ()
    return tuple(bead for bead, changed in landed if collided & set(changed))


def coupled_lanes(
    conflicts: tuple[str, ...],
    scopes: Mapping[str, tuple[str, ...]],
    *,
    bounced: str,
) -> tuple[str, ...]:
    """Lanes in *scopes* whose declared globs cover a conflicting path (pure, D9).

    The conflicting paths are the evidence a coupling was missed; the declared
    ``## Scope`` globs say *whose* coupling it is. Attributing from the plan this
    way rather than from what each landing happened to change is what makes the
    edge order-free: two lanes whose scopes collide name the same pair whichever
    of them lands first — and so whichever of them bounces (basicly-kjc5.32).

    Engine-owned paths are excluded for the same reason as in
    :func:`missed_couplings`. *bounced* is never its own culprit, results are
    sorted so the caller cannot inherit a dict's insertion order, and a lane with
    no readable declared scope is not a candidate: nothing shows it owns the path,
    and a wrong edge teaches the graph a coupling that does not exist.
    """
    collided = {path for path in conflicts if not _engine_owned(path)}
    if not collided:
        return ()
    return tuple(
        bead
        for bead, scope in sorted(scopes.items())
        if bead != bounced and _scope_covers(scope, collided)
    )


def branch_changed_paths(repo_root: Path, base: str, branch: str) -> tuple[str, ...]:
    """Paths *branch* changed relative to its merge base with *base* (sorted).

    The three-dot form, so a base that moved on after the lane forked is not
    counted as the lane's work — this is what the lane *did*, which is the only
    thing its declared scope can be held to (basicly-jr0l.44).

    Empty when git cannot answer, and empty by construction for the two states the
    landing already recognises: a branch with no commits, and one that already
    merged (its head is then the merge base). Best-effort like every other read on
    this path — a scope check that cannot be computed costs a finding, not the pass.
    """
    proc = git(["diff", "--name-only", f"{base}...{branch}"], cwd=repo_root, check=False)
    if proc.returncode != 0:
        return ()
    return tuple(sorted({line.strip() for line in proc.stdout.splitlines() if line.strip()}))


def out_of_scope_paths(changed: Iterable[str], scope: tuple[str, ...]) -> tuple[str, ...]:
    """The paths in *changed* that no declared glob in *scope* covers (pure, sorted).

    The declared scope is a planning input at decompose time and nothing checked it
    again afterwards, so a wrong declaration surfaced only later and indirectly, as
    a merge-queue conflict between two lanes that had already done fighting work
    (basicly-jr0l.44). This is the same question asked at the landing, where the
    lane's actual diff is finally available.

    An empty *scope* yields nothing: a bead that declared no scope — anything not
    created by ``decompose`` — contradicts no plan, and reporting every path it
    touched would be noise on every hand-filed leaf. Engine-owned paths are
    excluded for the reason :func:`coupled_lanes` excludes them: the harness
    rewrites the tracker on every landing, so no plan declares it and every lane
    would otherwise "violate".
    """
    if not scope:
        return ()
    return tuple(
        sorted(
            path
            for path in {raw.strip() for raw in changed if raw.strip()}
            if not _engine_owned(path) and not _scope_covers(scope, {path})
        )
    )


def _scope_covers(scope: tuple[str, ...], paths: set[str]) -> bool:
    """True when any declared glob in *scope* can match any path in *paths*.

    Reuses the decomposition's own glob overlap (a concrete path is a glob that
    matches only itself), so "in scope" means the same thing to the planner that
    declared the lanes parallel-safe and to the merge that proved it wrong.
    """
    return any(decompose.globs_overlap(path, glob) for glob in scope for path in paths)


def declared_scopes(repo_root: Path, beads: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Each bead's declared ``## Scope`` globs per ``br``; unreadable ones are absent.

    Best-effort like every other tracker read on the landing path: a bead whose
    scope cannot be read simply cannot be attributed, which costs the graph an
    edge rather than the pass.
    """
    scopes: dict[str, tuple[str, ...]] = {}
    for bead in beads:
        found = decompose.bead_class_and_scope(repo_root, bead)
        if found is not None:
            scopes[bead] = found[1]
    return scopes


def attribute_couplings(
    repo_root: Path,
    collisions: Sequence[tuple[str, tuple[str, ...]]],
    landed: Sequence[str],
) -> dict[str, tuple[str, ...]]:
    """The missed coupling per collided lane, computed once over the whole pass (D9).

    *collisions* is ``(bounced bead, conflicting paths)`` per bounce and *landed*
    the beads that landed anywhere in the pass. Deliberately **not** incremental:
    a bounce is attributed against every landing of the pass, not merely the
    landings that happened to precede it, so the prefix a scheduler produced
    cannot decide who receives the edge — nor whether one is recorded at all
    (basicly-kjc5.32). With :func:`coupled_lanes` reading declared scopes, the
    result is a function of the plan and the conflicting paths alone.
    """
    if not collisions or not landed:
        return {}
    scopes = declared_scopes(repo_root, dict.fromkeys(landed))
    return {bead: coupled_lanes(conflicts, scopes, bounced=bead) for bead, conflicts in collisions}


def record_pass_couplings(
    repo_root: Path,
    collisions: Sequence[tuple[str, tuple[str, ...]]],
    landed: Sequence[str],
) -> dict[str, tuple[str, ...]]:
    """Attribute a finished pass's collisions and write the edges (D5/D9).

    The one seam both landing paths — :func:`merge_queue` and the supervisor's
    ``route_outcomes`` — go through, so neither can drift back to an incremental
    attribution. Returns what was attributed, for the caller's own reporting.
    """
    attributed = attribute_couplings(repo_root, collisions, landed)
    for bead, culprits in attributed.items():
        for culprit in culprits:
            record_coupling(repo_root, bead, culprit)
    return attributed


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

    The pair is written in a **canonical direction** — the two ids sorted — so the
    edge is literally identical however the collision was discovered
    (basicly-kjc5.32). Coupling is symmetric and ``related`` does not gate, so the
    direction carried no meaning; what it did carry was which lane happened to
    land first, and that is exactly the pass-order leak into permanent graph state
    D9 forbids.

    Best-effort: a rejected edge (already present, or a cycle ``br`` refuses)
    must not turn a bounced lane into a crash. Note ``br`` refuses a duplicate
    rather than changing its type, so an edge some other path already recorded as
    ``blocks`` keeps that type.
    """
    first, second = sorted((bead, coupled_to))
    br.try_run_br(repo_root, ["dep", "add", first, second, "-t", COUPLING_DEP_TYPE])


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
    (rework recorded, the collision noted) and the pass continues with the lanes
    that can still land. A failed verify or a rejected merge commit still stops
    the pass — that is a signal about the base, not about one lane's scope.

    The missed couplings are written to the graph once, after the pass, by
    :func:`_attribute_pass` — never as each bounce happens, which would attribute
    against whatever prefix had landed by then (D9, basicly-kjc5.32).
    """
    config = config or load_policy_config(repo_root)
    results: list[QueueResult] = []
    landed: list[str] = []
    # (index in *results*, bead, conflicting paths) per bounce this pass. The
    # index is carried because a MergeResult names its worktree, not its bead.
    collisions: list[tuple[int, str, tuple[str, ...]]] = []
    order = landing_order(repo_root, items)
    # Snapshot every lane's branch head as the queue is formed, so a lane that grows
    # a commit while an earlier lane is landing is refused rather than landed in a
    # state this pass never examined (basicly-jr0l.46). Read once, up front: reading
    # it per lane at its own turn would defeat the point.
    queued_heads = {name: _session_branch_head(repo_root, name) for name, _ in order}
    for name, bead in order:
        result = merge_worktree(
            repo_root,
            name,
            bead=bead,
            verify_mode=verify_mode,
            expected_head=queued_heads.get(name),
        )
        if result.merged:
            landed.append(bead)
            results.append(QueueResult(result))
            continue
        if result.status == ALREADY_LANDED:
            # This lane's work is in base from an earlier, interrupted pass. Charge
            # nothing and keep going — writing the gate down is the loop's job, not
            # the queue's (basicly-jr0l.50). Deliberately not added to *landed*: the
            # couplings are attributed against the prefix a lane landed on, and that
            # prefix belonged to the earlier pass, not this one (D9).
            results.append(QueueResult(result))
            continue
        if result.status in ("not-ready", STALE_BRANCH):
            # Operator-fixable (work not committed on the branch), or the branch
            # moved under the queue. Both are states rather than merit failures and
            # nothing this pass can resolve: leave the lane queued, spend no rework
            # attempt on it, and let the lanes behind it land.
            results.append(QueueResult(result))
            continue
        if result.status == MERGE_UNPROVEN:
            # The merge claimed success and could not be proved. No evidence against
            # the lane, so charge nothing — but stop: base is in a state no further
            # landing may be stacked on (basicly-jr0l.46).
            results.append(QueueResult(result))
            break
        if result.unreliable:
            # The gate failed and then passed unchanged, so there is no evidence
            # against this lane: record the flake and leave it queued rather than
            # charging its bounded budget (basicly-55yh). Deliberately `continue`
            # where "verify-failed" breaks — a failure that does not reproduce is
            # not the signal about the base that stopping the pass exists for.
            policy.record_unreliable_gate(repo_root, bead, MERGE_GATE, result.detail)
            results.append(QueueResult(result))
            continue
        if result.foreign:
            # A tracker-wide gate failed on another lane's finishing record. No
            # evidence against this lane, so charge nothing — attribute it to the
            # lanes that did invalidate it (basicly-qorx). Deliberately `break`
            # where an unreliable gate continues: the record is durable and the gate
            # asserts over the whole shared tracker, so every lane behind this one
            # would pay a full verify run to reach the identical verdict. That is the
            # signal about the base stopping the pass exists for.
            policy.record_shared_gate_failure(
                repo_root, bead, MERGE_GATE, result.culprits, result.detail
            )
            results.append(QueueResult(result))
            break
        if result.conflicted:
            collisions.append((len(results), bead, result.conflicts))
            results.append(_bounce_back(repo_root, bead, result, config))
            continue
        attempts = policy.record_rework(repo_root, bead, MERGE_GATE)
        escalate = attempts >= config.max_rework
        results.append(QueueResult(result, attempts=attempts, escalate=escalate))
        break  # a bad base, not a bad lane: stop before stacking more on it
    return _attribute_pass(repo_root, results, collisions, landed)


def _bounce_back(
    repo_root: Path,
    bead: str,
    result: MergeResult,
    config: PolicyConfig,
) -> QueueResult:
    """Hand a conflicting lane back to its owner; the graph learns after the pass.

    No merge-time resolution of any kind (D5): the base was left untouched by
    :func:`merge_worktree`, the lane keeps its own commits, and re-applying the
    intent on the new base is the lane agent's job at its next dispatch — bounded
    by the rework cap, escalating to a human at it.

    The rework attempt is charged now (it is this lane's own budget); the coupling
    edge is not, because who to name is not knowable until the pass is done
    (:func:`_attribute_pass`).
    """
    attempts = policy.record_rework(repo_root, bead, MERGE_GATE)
    return QueueResult(
        result,
        attempts=attempts,
        escalate=attempts >= config.max_rework,
        bounced=True,
    )


def _attribute_pass(
    repo_root: Path,
    results: list[QueueResult],
    collisions: list[tuple[int, str, tuple[str, ...]]],
    landed: list[str],
) -> list[QueueResult]:
    """Write the pass's missed couplings and report them on the bounced results (D9).

    Runs once, over every landing of the pass, so a bounce is attributed the same
    way whether the lane it collided with landed before or after it
    (basicly-kjc5.32).
    """
    if not collisions:
        return results
    attributed = record_pass_couplings(
        repo_root, [(bead, conflicts) for _, bead, conflicts in collisions], landed
    )
    for index, bead, _ in collisions:
        culprits = attributed.get(bead, ())
        if culprits:
            bounced = results[index]
            results[index] = QueueResult(
                bounced.result,
                attempts=bounced.attempts,
                escalate=bounced.escalate,
                bounced=bounced.bounced,
                couplings=culprits,
            )
    return results


def head_sha(repo_root: Path) -> str:
    """The base checkout's HEAD, or "" when it cannot be read (never fatal)."""
    proc = git(["rev-parse", "HEAD"], cwd=repo_root, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def changed_paths(repo_root: Path, before: str) -> tuple[str, ...]:
    """Paths a landing added to the base since *before* (empty when unknown).

    Public because the supervisor lands lane by lane through the loop engine
    rather than through :func:`merge_queue` (kjc5.20) and D6's pre-empt needs to
    say *which* landing broke a pending merge (:func:`missed_couplings`). Not used
    for the coupling edge, which reads declared scopes instead so it cannot depend
    on landing order (:func:`coupled_lanes`).
    """
    if not before:
        return ()
    proc = git(["diff", "--name-only", f"{before}..HEAD"], cwd=repo_root, check=False)
    if proc.returncode != 0:
        return ()
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())
