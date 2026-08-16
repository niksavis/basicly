"""Replay a lane's branch onto its base, or say precisely what stopped it.

The boundary is *getting the branch onto base* against *merging it into base*:
:mod:`basicly.merge` owns the non-destructive probe, the ``--no-ff`` merge commit and
the ancestry proof that follows; this module owns only the replay and the two things
that can silently corrupt it. Split out of ``merge`` when the module-size ratchet
caught that module frozen at 15,401 tokens with a landing integrity guard still to add
(basicly-5vu4).

It does not import back. The caller owns ``MergeResult``; this module reports a
:class:`ReplayOutcome` carrying a status string the caller maps, for the reason
``plan_record`` satisfies ``plan_gate`` structurally rather than importing it.

The invariant the whole module exists for: **a replay never both drops content and
reports success.** ``git rebase`` skips merge commits unless ``--rebase-merges`` is
passed, so a lane that resolved a conflict with ``git merge`` — producing a resolution
that exists in neither parent — had that resolution silently deleted while the rebase
printed ``Successfully rebased and updated refs/heads/<branch>`` and exited 0. It
happened twice on 2026-08-08, on ``basicly-u2hl.20`` and ``basicly-u2hl.14``, in one
session. On ``u2hl.20`` **the suite stayed green afterwards**, because the feature and
the tests covering it were in the same dropped commit, so removing both left a
consistent tree that no longer did the thing the bead shipped. No gate can catch that
shape: there is nothing left to fail. Only a comparison against the pre-replay tree
can, which is why :func:`dropped_paths` is a check over the tree and not over
behaviour.

Two guards, deliberately both. :func:`merge_commits` refuses the known cause *before*
the rebase runs, so nothing is destroyed and the message can name the commit.
:func:`dropped_paths` is the general backstop over the result, because refusing a known
cause only covers the cause that is known.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import load_worktree_config
from .worktree import git, run

# Status for a branch carrying a merge commit `git rebase` would skip. Refused rather
# than rebased: `--rebase-merges` replays the merge and can re-raise the same conflicts
# with no attended resolver, and landing by merge instead would put an unreviewed
# second parent onto base. Handing it back to the lane, which still holds every commit,
# is the only option that loses nothing.
MERGE_COMMIT_ON_BRANCH = "rebase-merge-commit"

# Status for a replay that ran to completion and lost tracked content anyway. Distinct
# from MERGE_COMMIT_ON_BRANCH because it is the backstop firing on a cause nobody
# enumerated, and an operator needs to know which of the two spoke.
REPLAY_DROPPED_PATHS = "rebase-dropped-paths"

# Runaway backstop for :func:`rebuild_generated_conflicts`. Each pass through the loop
# resolves every unmerged path and advances the rebase by one commit, so a branch needs
# more commits than this to reach it legitimately; a rebase that has not finished by
# then is not understood, and an un-understood rebase is aborted rather than driven
# further.
MAX_REGENERATED_REBASE_STEPS = 100

_CONFLICT_MARKER = re.compile(rb"^(?:<{7}|>{7})", re.MULTILINE)


@dataclass(frozen=True)
class ReplayOutcome:
    """What the replay did, in the form the caller needs to build its own result.

    *status* is empty exactly when the replay succeeded; the caller maps a non-empty
    one onto its own result type rather than this module knowing about it.
    """

    status: str = ""
    detail: str = ""
    # Paths git reported unmerged, for a stopped rebase. Carried as data because the
    # queue attributes a missed coupling from them (D5) and must not parse the message.
    conflicts: tuple[str, ...] = ()
    # Generated artifacts rebuilt to finish a stopped rebase, for the caller to report.
    regenerated: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when the branch is now replayed onto base with nothing lost."""
        return not self.status


def unmerged_paths(cwd: Path) -> tuple[str, ...]:
    """Paths git currently reports as unmerged in *cwd* (empty when none/unknown)."""
    proc = git(["diff", "--name-only", "--diff-filter=U"], cwd=cwd, check=False)
    if proc.returncode != 0:
        return ()
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


def merge_commits(cwd: Path, base: str, branch: str) -> tuple[str, ...]:
    """Merge commits on *branch* that *base* does not already contain.

    Empty is the normal answer and also the answer when git cannot be read — a replay
    that cannot be inspected still gets :func:`dropped_paths` over its result, so an
    unreadable probe degrades to the backstop rather than to a false refusal that would
    strand every landing.
    """
    proc = git(["rev-list", "--merges", f"{base}..{branch}"], cwd=cwd, check=False)
    if proc.returncode != 0:
        return ()
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


def _tree_paths(cwd: Path, ref: str) -> frozenset[str] | None:
    """Every tracked path in *ref*'s tree, or None when *ref* cannot be read."""
    proc = git(["ls-tree", "-r", "--name-only", ref], cwd=cwd, check=False)
    if proc.returncode != 0:
        return None
    return frozenset(line.strip() for line in proc.stdout.splitlines() if line.strip())


def dropped_paths(cwd: Path, before: str, base: str) -> tuple[str, ...]:
    """Tracked paths *before* carried that the replayed tree lost, excluding base's own deletions.

    A path absent after the replay is legitimate when *base* is what removed it: it
    existed at the merge base, base deleted it, and the branch merely inherited it. That
    is the one subtraction, and without it every landing onto a base that deleted a file
    would report a false drop.

    Empty when any ref cannot be read. A guard that cannot see is reported by the caller
    as a passing replay rather than as a failing one, because an unreadable tree is a
    property of the probe and refusing every landing on it would be the worse failure.
    """
    before_paths = _tree_paths(cwd, before)
    after_paths = _tree_paths(cwd, "HEAD")
    if before_paths is None or after_paths is None:
        return ()
    lost = before_paths - after_paths
    if not lost:
        return ()
    fork = git(["merge-base", base, before], cwd=cwd, check=False)
    if fork.returncode != 0:
        return tuple(sorted(lost))
    forked_paths = _tree_paths(cwd, fork.stdout.strip())
    base_paths = _tree_paths(cwd, base)
    if forked_paths is None or base_paths is None:
        return tuple(sorted(lost))
    return tuple(sorted(lost - (forked_paths - base_paths)))


def unresolved(path: Path) -> bool:
    """True when *path* still carries a conflict marker the rebuild did not remove.

    What makes a *partly* generated file declarable (basicly-3w51): a rebuild writing
    only its own block leaves the prose around it conflicted, and staging that commits
    markers over a hand-written change. Bytes at column 0 and only the two unambiguous
    markers, since ``=======`` is a legal setext rule; an unreadable path carries none.
    """
    try:
        blob = path.read_bytes()
    except OSError:
        return False
    return _CONFLICT_MARKER.search(blob) is not None


def _rebuilt_clean(
    worktree_path: Path, conflicts: tuple[str, ...], commands: Mapping[str, tuple[str, ...]]
) -> bool:
    """Run each conflicted path's own rebuild; True when none of them still conflicts."""
    for path in conflicts:
        if run(list(commands[path]), cwd=worktree_path, check=False).returncode != 0:
            return False
    return not any(unresolved(worktree_path / path) for path in conflicts)


def refresh_generated(repo_root: Path, worktree_path: Path, bead: str) -> tuple[str, ...]:
    """Rebuild every declared generated path against the tree the rebase produced.

    :func:`rebuild_generated_conflicts` fires on a *conflict*. Staleness needs no
    conflict: a lane that merely adds a module leaves ``plan-current-state`` counting a
    tree that no longer exists, and that path is outside every lane's scope, so the lane
    is refused for a defect it is not permitted to repair and spends its whole rework
    budget finding out (basicly-e2mz.35).

    Committed rather than left in the tree, because the landing merges the *branch*: an
    uncommitted rebuild would pass the verify below and then never reach the base.
    """
    rebuilt = []
    for path, command in sorted(load_worktree_config(repo_root).regenerate_commands.items()):
        if run(list(command), cwd=worktree_path, check=False).returncode != 0:
            continue
        if git(["diff", "--quiet", "--", path], cwd=worktree_path, check=False).returncode != 0:
            rebuilt.append(path)
    if not rebuilt or git(["add", "--", *rebuilt], cwd=worktree_path, check=False).returncode != 0:
        return ()
    message = f"chore(regen): rebuild the artifacts the rebase left stale ({bead})"
    if git(["commit", "-m", message], cwd=worktree_path, check=False).returncode != 0:
        return ()
    return tuple(rebuilt)


def rebuild_generated_conflicts(repo_root: Path, worktree_path: Path) -> tuple[str, ...] | None:
    """Finish a stopped rebase whose conflicts are all declared generated (basicly-lyro).

    Returns the rebuilt paths when the rebase now runs to completion, and None when
    the caller must abort and bounce — which is every case that is not provably this
    one. The bound is deliberate and is what keeps the merge queue's "never resolve a
    conflict here" rule intact for source: this resolves only when *every* unmerged
    path is keyed in ``[worktree.regenerate_commands]``, so one undeclared path in the
    set hands the whole rebase back to the lane untouched.

    Regeneration is not a merge. Both sides are discarded and the artifact is rebuilt
    from the tree the rebase has actually produced, because a generated file is a
    function of that tree and picking a side would leave it describing neither parent
    — which is exactly what a three-lane catalog pass hit on
    ``.basicly/generated-manifest.json``, spending a lane's whole rework budget.

    A residual staleness is caught rather than shipped: the artifact is rebuilt at
    each stop, so the last stop that touches it sees the tree the rebase ends with,
    and anything that slips past that is caught by the post-rebase verify gate (the
    ``projection-*`` checks fail on a stale projection), which bounces the lane as it
    does today. Nothing here can land a wrong artifact silently.
    """
    commands = load_worktree_config(repo_root).regenerate_commands
    if not commands:
        return None

    rebuilt: set[str] = set()
    for _ in range(MAX_REGENERATED_REBASE_STEPS):
        conflicts = unmerged_paths(worktree_path)
        if not conflicts or not commands.keys() >= set(conflicts):
            return None
        if not _rebuilt_clean(worktree_path, conflicts, commands):
            return None
        # `git add` on an unmerged path is what marks it resolved, whether or not the
        # rebuild changed its bytes.
        if git(["add", "--", *conflicts], cwd=worktree_path, check=False).returncode != 0:
            return None
        rebuilt.update(conflicts)
        # core.editor=true: `rebase --continue` reuses the replayed commit's message
        # but still opens an editor to confirm it, and nothing is attended here.
        proceed = git(
            ["-c", "core.editor=true", "rebase", "--continue"], cwd=worktree_path, check=False
        )
        if proceed.returncode == 0:
            return tuple(sorted(rebuilt))
    return None


def replay(repo_root: Path, worktree_path: Path, base: str, branch: str) -> ReplayOutcome:
    """Rebase *branch* onto *base* in *worktree_path*, refusing rather than losing work.

    Restores the branch to its pre-replay tip when the integrity guard fires. That reset
    is a restoration and not a discard — the target is the exact commit the replay
    started from, so the lane keeps every commit it had and can linearize and re-land.
    """
    carried = merge_commits(worktree_path, base, branch)
    if carried:
        return ReplayOutcome(
            MERGE_COMMIT_ON_BRANCH,
            f"{branch} carries {len(carried)} merge commit(s) `git rebase` would skip, "
            f"discarding any resolution held only there: {', '.join(carried)}. "
            "Linearize the branch (rebase it onto the merged base in the lane) and re-land.",
        )

    before = git(["rev-parse", branch], cwd=worktree_path, check=False)
    tip = before.stdout.strip() if before.returncode == 0 else ""

    regenerated: tuple[str, ...] = ()
    rebase = git(["rebase", base, branch], cwd=worktree_path, check=False)
    if rebase.returncode != 0:
        rebuilt = rebuild_generated_conflicts(repo_root, worktree_path)
        if rebuilt is None:
            # Read the collided paths out of the stopped rebase before aborting: the
            # queue needs them to attribute the missed coupling (D5), and they are
            # gone once the rebase state is discarded.
            conflicts = unmerged_paths(worktree_path)
            git(["rebase", "--abort"], cwd=worktree_path, check=False)
            where = f" in: {', '.join(conflicts)}" if conflicts else ""
            return ReplayOutcome(
                "rebase-conflicts",
                f"rebase of {branch} onto {base} hit conflicts{where}",
                conflicts=conflicts,
            )
        regenerated = rebuilt

    if tip:
        lost = dropped_paths(worktree_path, tip, base)
        if lost:
            git(["reset", "--hard", tip], cwd=worktree_path, check=False)
            return ReplayOutcome(
                REPLAY_DROPPED_PATHS,
                f"rebase of {branch} onto {base} reported success but lost "
                f"{len(lost)} tracked path(s): {', '.join(lost)}. "
                f"{branch} has been restored to {tip[:12]}; nothing was landed.",
            )

    return ReplayOutcome(regenerated=regenerated)
