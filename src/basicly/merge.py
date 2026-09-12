from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from . import (
    base_lock,
    decompose,
    owned_store,
    policy,
    rebase,
    run_record,
    tracker,
    tracker_paths,
    verify,
)
from .config import PolicyConfig, load_policy_config, load_worktree_config
from .worktree import Session, current_branch, git, load_session

MERGE_GATE = "merge"

CONFLICT_STATUSES = ("rebase-conflicts", "merge-conflicts")

ENGINE_PATHS = (owned_store.LEDGER_DIR.as_posix() + "/",)

COUPLING_DEP_TYPE = "related"

VERIFY_UNRELIABLE = "verify-unreliable"

VERIFY_FOREIGN = "verify-foreign"

STALE_BRANCH = "stale-branch"

MERGE_UNPROVEN = "merge-unproven"

ALREADY_LANDED = "already-landed"

PRE_GATE_STATUSES = (
    "not-ready",
    STALE_BRANCH,
    ALREADY_LANDED,
    "rebase-conflicts",
    rebase.MERGE_COMMIT_ON_BRANCH,
    rebase.REPLAY_DROPPED_PATHS,
)


@dataclass(frozen=True)
class ProbeResult:
    safe: bool
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class MergeResult:
    name: str
    status: str
    detail: str
    conflicts: tuple[str, ...] = ()
    checks: tuple[verify.CheckResult, ...] = ()
    culprits: tuple[str, ...] = ()
    landed_head: str = ""

    @property
    def merged(self) -> bool:
        return self.status == "merged"

    @property
    def conflicted(self) -> bool:
        return self.status in CONFLICT_STATUSES

    @property
    def unreliable(self) -> bool:

        return self.status == VERIFY_UNRELIABLE

    @property
    def foreign(self) -> bool:

        return self.status == VERIFY_FOREIGN

    @property
    def reached_gate(self) -> bool:

        return self.status not in PRE_GATE_STATUSES


def _shared_tracker_failure(
    repo_root: Path, report: verify.VerifyReport, bead: str
) -> policy.SharedGateFailure | None:

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


def _unrebuilt_generated(repo_root: Path, report: verify.VerifyReport) -> tuple[str, ...]:

    output = "".join(r.output for r in report.results if r.status == "fail")
    return tuple(
        f"`{path}` <- `{' '.join(argv)}`"
        for path, argv in sorted(load_worktree_config(repo_root).regenerate_commands.items())
        if path in output
    )


def _verify_for_landing(
    name: str, worktree_path: Path, verify_mode: str, clock: _Landing
) -> MergeResult | None:

    report = verify.run_verify(worktree_path, verify_mode)
    clock.note_checks(report)
    if report.passed:
        owed = verify.release_note_debt(clock.repo_root, worktree_path, verify_mode, clock.bead)
        detail = f"{verify.RELEASE_NOTES_CHECK} refuses this landing: {owed}"
        return MergeResult(name, "verify-failed", detail) if owed else None
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
    if (shared := _shared_tracker_failure(clock.repo_root, rerun, clock.bead)) is not None:
        return MergeResult(
            name,
            VERIFY_FOREIGN,
            f"verify {verify_mode} failed on {failures} — invalidated in the shared "
            f"tracker by {', '.join(shared.culprits)}, not by this lane's diff: "
            f"{shared.reason}",
            culprits=shared.culprits,
        )
    if unrebuilt := _unrebuilt_generated(clock.repo_root, rerun):
        return MergeResult(
            name,
            "verify-failed",
            f"verify {verify_mode} failed on {failures}: the rebuild this landing ran before "
            f"the gate did not make a declared generated path current — {'; '.join(unrebuilt)}",
            checks=_failed_checks(rerun),
        )
    return MergeResult(
        name,
        "verify-failed",
        f"verify {verify_mode} failed: {_verify_reasons(rerun) or failures}",
        checks=_failed_checks(rerun),
    )


def _failed_checks(rerun: verify.VerifyReport) -> tuple[verify.CheckResult, ...]:
    return tuple(result for result in rerun.results if result.status == "fail")


def _verify_reasons(rerun: verify.VerifyReport) -> str:

    named = []
    for result in rerun.results:
        if result.status != "fail":
            continue
        if remedy := verify.check_remedy(result.output, result.name):
            named.append(f"{result.name}: {remedy}")
    return "; ".join(named)


def probe_merge(repo_root: Path, base: str, branch: str) -> ProbeResult:

    proc = git(
        ["merge-tree", "--write-tree", "--name-only", base, branch],
        cwd=repo_root,
        check=False,
    )
    if proc.returncode == 0:
        return ProbeResult(safe=True, conflicts=())
    lines = proc.stdout.splitlines()
    conflicts = tuple(lines[1:] if len(lines) > 1 else lines)
    return ProbeResult(safe=False, conflicts=conflicts)


def _session_branch_head(repo_root: Path, name: str) -> str | None:

    try:
        session = load_session(name, repo_root)
    except OSError, RuntimeError:
        return None
    return branch_head(repo_root, session.branch) if session is not None else None


def branch_head(repo_root: Path, branch: str) -> str | None:

    proc = git(["rev-parse", "--verify", f"refs/heads/{branch}"], cwd=repo_root, check=False)
    head = proc.stdout.strip()
    return head if proc.returncode == 0 and head else None


def carried_commits(repo_root: Path, base: str, branch: str) -> int | None:

    proc = git(["rev-list", "--count", f"{base}..{branch}"], cwd=repo_root, check=False)
    count = proc.stdout.strip()
    return int(count) if proc.returncode == 0 and count.isdigit() else None


def is_ancestor(repo_root: Path, commit: str, target: str) -> bool:

    return (
        git(["merge-base", "--is-ancestor", commit, target], cwd=repo_root, check=False).returncode
        == 0
    )


def _assert_base_ready(repo_root: Path, base: str) -> None:
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


ENGINE_TRACKER_PATHS = (owned_store.LEDGER_DIR.as_posix(),)

EVENT_LOG_GLOB = "events-*.jsonl"


def _under(path: str, tree: str) -> bool:
    return path.startswith(f"{tree}/")


def is_engine_tracker_path(path: str) -> bool:

    return any(_under(path, tree) for tree in ENGINE_TRACKER_PATHS)


def foreign_dirt(repo_root: Path) -> tuple[str, ...]:

    lines = git(["status", "--porcelain"], cwd=repo_root).stdout.splitlines()
    paths = [line[3:] for line in lines if line.strip()]
    return tuple(path for path in paths if not is_engine_tracker_path(path))


def skipped_tracker_commit_warning(repo_root: Path) -> str:

    foreign = foreign_dirt(repo_root)
    if not foreign:
        return ""
    return (
        "WARNING tracker state NOT committed — these paths are dirty in the base "
        f"checkout and are not the loop's to commit: {', '.join(foreign)}; stash or "
        "commit them and re-run the advance to publish the tracker state"
    )


class TrackerCommitRefusedError(RuntimeError):
    pass


def commit_tracker_state(
    repo_root: Path,
    bead: str,
    *,
    action: str = "sync tracker state for the harness loop",
    wait_s: float = base_lock.WAIT_S,
    on_retry: Callable[[str], None] | None = None,
) -> bool:

    with base_lock.hold(repo_root, wait_s=wait_s):
        return _commit_tracker_state(repo_root, bead, action=action, on_retry=on_retry)


def _commit_tracker_state(
    repo_root: Path, bead: str, *, action: str, on_retry: Callable[[str], None] | None = None
) -> bool:
    lines = git(["status", "--porcelain"], cwd=repo_root).stdout.splitlines()
    paths = [line[3:] for line in lines if line.strip()]
    if not paths or not all(is_engine_tracker_path(path) for path in paths):
        return False
    tracker.scrub_ledger(repo_root)
    dirty = [tree for tree in ENGINE_TRACKER_PATHS if any(_under(path, tree) for path in paths)]
    git(["add", *dirty], cwd=repo_root)
    _commit_staged_tracker_state(repo_root, f"chore(beads): {action} ({bead})", on_retry)
    return True


def _commit_staged_tracker_state(
    repo_root: Path, message: str, on_retry: Callable[[str], None] | None
) -> None:

    try:
        git(["commit", "-m", message], cwd=repo_root)
    except RuntimeError as first:
        refused = str(first)
    else:
        return
    try:
        git(["commit", "-m", message], cwd=repo_root)
    except RuntimeError as again:
        raise TrackerCommitRefusedError(str(again)) from again
    if on_retry is not None:
        on_retry(f"tracker-sync commit refused once and taken on a retry ({refused})")


def known_bead_ids(repo_root: Path) -> set[str] | None:

    ledger = tracker_paths.ledger_dir(repo_root)
    ids: set[str] = set()
    for log in sorted(ledger.glob(EVENT_LOG_GLOB)):
        for raw_line in log.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and isinstance(event.get("record"), str):
                ids.add(event["record"])
    return ids or None


def _merge_message(
    name: str, branch: str, base: str, bead: str, record: run_record.RunRecord | None = None
) -> str:

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

    name, branch = session.name, session.branch
    forced = _worktree_land_readiness(repo_root, session)
    if forced is not None:
        return forced

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

    record = run_record.latest_record(repo_root, bead)
    carried = carried_commits(repo_root, base, branch)
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
    head = git(["rev-parse", "--short", "HEAD"], cwd=repo_root).stdout.strip()
    took = f"{carried} commit(s)" if carried is not None else "an uncounted number of commits"
    return MergeResult(
        name,
        "merged",
        f"merged {branch} @ {landed_head[:12]} ({took}) into {base} @ {head}",
        landed_head=landed_head,
    )


LANDING_TIMINGS_FILE = Path(".basicly/usage/landing-timings.json")

_SLOWEST_CHECKS = 4


class _Landing:
    def __init__(
        self, repo_root: Path, bead: str, clock: Callable[[], float] = time.perf_counter
    ) -> None:
        self.repo_root = repo_root
        self.bead = bead
        self.clock = clock
        self.started = clock()
        self._at = self.started
        self.stages: list[tuple[str, float]] = []
        self.checks: tuple[tuple[str, float], ...] = ()

    def mark(self, stage: str) -> None:

        now = self.clock()
        self.stages.append((stage, round(now - self._at, 3)))
        self._at = now

    def note_checks(self, report: verify.VerifyReport) -> None:
        ranked = sorted(((r.name, r.duration_s) for r in report.results), key=lambda c: -c[1])
        self.checks = tuple(ranked[:_SLOWEST_CHECKS])

    def close(self, result: MergeResult) -> MergeResult:

        total = round(self.clock() - self.started, 3)
        attributed = round(sum(seconds for _, seconds in self.stages), 3)
        _write_landing_timing(
            self.repo_root,
            self.bead,
            {
                "recorded_at": datetime.now(UTC).isoformat(),
                "status": result.status,
                "total_s": total,
                "attributed_s": attributed,
                "unattributed_s": round(total - attributed, 3),
                "stages": [list(span) for span in self.stages],
                "slowest_checks": [list(check) for check in self.checks],
            },
        )
        if not result.merged:
            return result
        return replace(result, detail=f"{result.detail}; {self._headline(total)}")

    def _headline(self, total: float) -> str:
        top = sorted(self.stages, key=lambda span: -span[1])[:3]
        named = ", ".join(f"{stage} {seconds:.1f}s" for stage, seconds in top if seconds >= 0.05)
        return f"landing {total:.1f}s ({named})" if named else f"landing {total:.1f}s"


def _write_landing_timing(repo_root: Path, bead: str, entry: dict[str, object]) -> None:

    path = repo_root / LANDING_TIMINGS_FILE
    try:
        loaded = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except OSError, ValueError:
        loaded = {}
    try:
        records: dict[str, list] = loaded if isinstance(loaded, dict) else {}
        records.setdefault(bead, []).append(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        gitignore = path.parent / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError, ValueError:
        return


def merge_worktree(  # noqa: PLR0913 — one keyword per independent landing input
    repo_root: Path,
    name: str,
    *,
    bead: str,
    verify_mode: str = "full",
    expected_head: str | None = None,
    override_gate: bool = False,
) -> MergeResult:

    if not bead:
        raise SystemExit(
            "merge needs a bead id for the merge commit (the commit-msg hook requires one)"
        )
    clock = _Landing(repo_root, bead)
    known = known_bead_ids(repo_root)
    if known is not None and bead not in known:
        raise SystemExit(
            f"unknown bead id {bead!r}: the committed ledger does not hold it — the commit-msg "
            "hook would reject the merge commit and strand the base mid-merge"
        )

    session = load_session(name, repo_root)
    if session is None:
        raise SystemExit(f"no worktree session named {name!r}")

    blocked = _pre_merge_state(repo_root, session, expected_head)
    clock.mark("preflight")
    if blocked is not None:
        return clock.close(blocked)

    retried: list[str] = []
    if current_branch(repo_root) == session.base:
        commit_tracker_state(repo_root, bead, on_retry=retried.append)
    _assert_base_ready(repo_root, session.base)
    clock.mark("tracker-commit")

    landed = _replay_verify_merge(
        session, verify_mode=verify_mode, override_gate=override_gate, clock=clock
    )
    if retried:
        landed = replace(landed, detail=f"{landed.detail}; {retried[0]}")
    return clock.close(landed)


def _replay_verify_merge(
    session: Session, *, verify_mode: str, override_gate: bool, clock: _Landing
) -> MergeResult:

    repo_root, bead = clock.repo_root, clock.bead
    name, base, branch = session.name, session.base, session.branch
    worktree_path = session.path

    replayed = rebase.replay(repo_root, worktree_path, base, branch)
    clock.mark("rebase")
    if not replayed.ok:
        return MergeResult(name, replayed.status, replayed.detail, conflicts=replayed.conflicts)
    regenerated = replayed.regenerated + rebase.refresh_generated(repo_root, worktree_path, bead)
    clock.mark("regenerate")

    if not override_gate:
        gate = _verify_for_landing(name, worktree_path, verify_mode, clock)
        clock.mark("verify")
        if gate is not None:
            return gate

    probe = probe_merge(repo_root, base, branch)
    clock.mark("probe")
    if not probe.safe:
        return MergeResult(
            name,
            "merge-conflicts",
            f"conflicts in: {', '.join(probe.conflicts)}",
            conflicts=probe.conflicts,
        )

    landed = _merge_and_prove(repo_root, name, base=base, branch=branch, bead=bead)
    clock.mark("merge")
    if landed.merged and regenerated:
        landed = replace(landed, detail=f"{landed.detail} (regenerated {', '.join(regenerated)})")
    return landed


@dataclass(frozen=True)
class QueueResult:
    result: MergeResult
    attempts: int = 0
    escalate: bool = False
    bounced: bool = False
    couplings: tuple[str, ...] = ()

    @property
    def deferred(self) -> bool:

        return (
            self.result.status in ("not-ready", STALE_BRANCH)
            or self.result.unreliable
            or self.result.foreign
        )


def blocking_dependencies(repo_root: Path, bead: str) -> frozenset[str]:

    record = tracker.read_record(repo_root, bead)
    if record is None:
        return frozenset()
    blocking: set[str] = set()
    for dep in record.get("dependencies") or []:
        edge = tracker.dependency_edge(dep)
        if edge is not None and edge[1] == "blocks":
            blocking.add(edge[0])
    return frozenset(blocking)


def landing_order(repo_root: Path, items: list[tuple[str, str]]) -> list[tuple[str, str]]:

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

    collided = {path for path in conflicts if not _engine_owned(path)}
    if not collided:
        return ()
    return tuple(
        bead
        for bead, scope in sorted(scopes.items())
        if bead != bounced and _scope_covers(scope, collided)
    )


def branch_changed_paths(repo_root: Path, base: str, branch: str) -> tuple[str, ...]:

    proc = git(["diff", "--name-only", f"{base}...{branch}"], cwd=repo_root, check=False)
    if proc.returncode != 0:
        return ()
    return tuple(sorted({line.strip() for line in proc.stdout.splitlines() if line.strip()}))


def out_of_scope_paths(changed: Iterable[str], scope: tuple[str, ...]) -> tuple[str, ...]:

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

    return any(decompose.globs_overlap(path, glob) for glob in scope for path in paths)


def declared_scopes(repo_root: Path, beads: Iterable[str]) -> dict[str, tuple[str, ...]]:

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

    if not collisions or not landed:
        return {}
    scopes = declared_scopes(repo_root, dict.fromkeys(landed))
    return {bead: coupled_lanes(conflicts, scopes, bounced=bead) for bead, conflicts in collisions}


def record_pass_couplings(
    repo_root: Path,
    collisions: Sequence[tuple[str, tuple[str, ...]]],
    landed: Sequence[str],
) -> dict[str, tuple[str, ...]]:

    attributed = attribute_couplings(repo_root, collisions, landed)
    for bead, culprits in attributed.items():
        for culprit in culprits:
            record_coupling(repo_root, bead, culprit)
    return attributed


def _engine_owned(path: str) -> bool:

    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return any(normalized.lstrip("/").startswith(prefix) for prefix in ENGINE_PATHS)


def record_coupling(repo_root: Path, bead: str, coupled_to: str) -> None:

    first, second = sorted((bead, coupled_to))
    tracker.try_write(repo_root, ["dep", "add", first, second, "-t", COUPLING_DEP_TYPE])


def merge_queue(
    repo_root: Path,
    items: list[tuple[str, str]],
    *,
    config: PolicyConfig | None = None,
    verify_mode: str = "full",
) -> list[QueueResult]:

    config = config or load_policy_config(repo_root)
    results: list[QueueResult] = []
    landed: list[str] = []
    collisions: list[tuple[int, str, tuple[str, ...]]] = []
    order = landing_order(repo_root, items)
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
            results.append(QueueResult(result))
            continue
        if result.status in ("not-ready", STALE_BRANCH):
            results.append(QueueResult(result))
            continue
        if result.status == MERGE_UNPROVEN:
            results.append(QueueResult(result))
            break
        if result.unreliable:
            policy.record_unreliable_gate(repo_root, bead, MERGE_GATE, result.detail)
            results.append(QueueResult(result))
            continue
        if result.foreign:
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
        break
    return _attribute_pass(repo_root, results, collisions, landed)


def _bounce_back(
    repo_root: Path,
    bead: str,
    result: MergeResult,
    config: PolicyConfig,
) -> QueueResult:

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
    proc = git(["rev-parse", "HEAD"], cwd=repo_root, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def changed_paths(repo_root: Path, before: str) -> tuple[str, ...]:

    if not before:
        return ()
    proc = git(["diff", "--name-only", f"{before}..HEAD"], cwd=repo_root, check=False)
    if proc.returncode != 0:
        return ()
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())
