"""Pass contention: the collisions a pass can see before any lane starts.

One responsibility, and it is the shared path. A path this repo's conventions have
*every* lane append its own entry to appears in no bead's ``## Scope``, so the
disjoint-scope check that admits the pass is reading an incomplete list. Measured on
the basicly-u6jq.1 proof run: three lanes, provably disjoint scopes, ``VERDICT:
ready``, and the third lane bounced twice on a ``CHANGELOG.md`` rebase conflict and
spent its whole rework budget getting there.

Two reports, because the shared path has two shapes and they take opposite remedies.
:func:`append_only_report` names the paths a build order must separate;
:func:`generated_report` names the ones a landing rebuilds instead. They are printed
side by side for that reason — an operator who reads only the ``contend:`` line
concludes a shared artifact must serialise the pass when it need not (basicly-lyro).

Reported at preflight rather than only serialized at decompose, because the lanes that
collided were hand-filed siblings that no plan ever grouped: nothing in
:mod:`basicly.decompose` runs for them, and preflight is the only surface that sees
the whole lane set.

Split out of ``supervise`` when the module-size ratchet caught that module growing.
The boundary is *advice* against *admission*: nothing here refuses a pass or reads a
lane's size, which is what ``supervise``'s working-set band and spend ceiling do, and
that is why the split leaves no import back into the module it came from.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from . import corpus_drift, loop_state, merge, needs_input, tracker
from .config import lane_scope


def append_only_report(
    repo_root: Path, lanes: tuple[str, ...], paths: tuple[str, ...]
) -> tuple[str, ...]:
    """Whether this pass's lanes will contend on a configured append-only path.

    First line is coverage — which paths were checked, or that none is declared —
    for the reason :func:`supervise.band_coverage` exists: a check that prints nothing
    when it finds nothing is indistinguishable from a check that never ran, and this
    one is inert until a consumer lists a path. Then one line per path two or more
    lanes will each append to without declaring it.

    A lane that *declares* the path in its own ``## Scope`` is left out of the count:
    it has said out loud that it writes the file, so the scope-collision gate
    (``loop._scope_block``) and the band both already see it. The undeclared lanes are
    the population this bead is about.
    """
    if not paths:
        return (
            "no append-only path declared ([worktree] append_only_paths) - a path every "
            "lane writes is invisible to the grouping until it is listed",
        )
    header = f"append-only: {', '.join(f'`{path}`' for path in paths)}"
    if len(lanes) < 2:
        return (f"{header} - {len(lanes)} lane(s) in this pass, so nothing contends",)
    scopes = merge.declared_scopes(repo_root, lanes)
    lines = [header]
    for path in paths:
        contending = tuple(lane for lane in lanes if path not in scopes.get(lane, ()))
        if len(contending) < 2:
            continue
        lines.append(
            f"  {len(contending)} lane(s) will each append to `{path}` and none declares it: "
            f"{', '.join(contending)}"
        )
        lines.append(
            "    the later ones rebase onto a moved anchor and bounce, so build them in "
            "sequence, or give one lane the entry"
        )
    return tuple(lines)


def generated_report(commands: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    """What this pass will do with a landing conflict on a rebuildable artifact.

    Reported beside :func:`append_only_report` because the two are the same collision
    with opposite remedies, and an operator who reads only the ``contend:`` line would
    conclude a shared artifact must serialise the pass when it need not (basicly-lyro).

    Says so when nothing is declared, for the reason that report does: the undeclared
    state is the one that costs a lane its rework budget, and it is only ever
    discovered at the merge queue, after the money is spent.

    One line per path, since each carries its own rebuild command (basicly-3w51).
    """
    if not commands:
        return (
            "no generated path declared ([worktree.regenerate_commands]) - a landing conflict "
            "on an artifact every lane rebuilds bounces the lane instead of being rebuilt",
        )
    return (
        "generated: a landing conflict confined to these is rebuilt and continues, "
        "spending no rework",
        *(f"           `{path}` <- `{' '.join(argv)}`" for path, argv in sorted(commands.items())),
    )


# --- The same collision, told to the lane instead of to the operator ---------
#
# The two reports above reach an operator at preflight. Nothing reached the *lane*:
# `basicly brief <id>` printed neither the lane's own `## Scope` nor any sibling's, so
# a lane that needed a sibling-owned path had no route but to edit it and find out at
# the merge queue. Observed 2026-08-26 on the basicly-k6tpep pass — two of three
# siblings edited `board_render.py`, which the third declared; both landings blocked on
# `needs input: scope` and a human widened each record by hand (basicly-dy4f94).

# The fact `loop._scope_block` blocks a collision under. The brief has to route the lane
# to the same word a human will be answering, and a second spelling would file the
# lane's sentinel against a question nobody is holding.
SCOPE_FACT = "scope"


def with_scope_fence(repo_root: Path, issue_id: str, prompt: str) -> str:
    """*prompt* naming the paths *issue_id*'s open siblings own, or *prompt* unchanged.

    Unchanged is the common case and the deliberate one: a root, a hand-filed leaf, and
    a pass whose siblings declare no scope each have nothing to fence, and a brief that
    grew a section saying so would spend every lane's context on the answer "nobody".
    """
    owned = sibling_scopes(repo_root, issue_id)
    if not owned:
        return prompt
    return f"{prompt}\n\n{_fence(repo_root, issue_id, owned)}"


def sibling_scopes(repo_root: Path, issue_id: str) -> dict[str, tuple[str, ...]]:
    """Declared ``## Scope`` globs of *issue_id*'s still-dispatchable siblings.

    Siblings off the root's ``parent-child`` edge, deliberately not the live worktrees
    ``loop._live_lane_scopes`` reads. That set is the right one for the landing gate,
    which runs when every lane of the pass exists; a brief is assembled as often
    *before* a sibling is provisioned as after, and a lane told about the collision only
    once the other worktree is on disk has learnt it too late to plan around.

    ``loop_state.is_dispatchable`` decides which siblings still count, so a closed or
    deferred one is silent here for the same reason it draws no funding there. Lanes
    declaring no scope are dropped: the entry would name a lane and no ground.
    """
    record = tracker.read_record(repo_root, issue_id)
    if record is None:
        return {}
    siblings = {
        sibling
        for parent in _parent_ids(record)
        for sibling, status in corpus_drift.children_of_record(
            tracker.read_record(repo_root, parent) or {}
        ).items()
        if sibling != issue_id and loop_state.is_dispatchable(status)
    }
    scopes = merge.declared_scopes(repo_root, sorted(siblings))
    return {lane: paths for lane, paths in scopes.items() if paths}


def _parent_ids(record: Mapping[str, object]) -> tuple[str, ...]:
    """Every record *record* hangs off a ``parent-child`` edge from.

    Plural because the edge is read, not assumed: :func:`basicly.tracker.dependency_edge`
    is what reads both of the tracker's dependency spellings, and a bead with two parents
    yields the union of their children rather than a silently-picked one.
    """
    dependencies = record.get("dependencies")
    rows = dependencies if isinstance(dependencies, list) else []
    edges = (tracker.dependency_edge(dep) for dep in rows)
    return tuple(
        edge[0] for edge in edges if edge is not None and edge[1] == loop_state.PARENT_CHILD
    )


def _fence(repo_root: Path, issue_id: str, owned: Mapping[str, tuple[str, ...]]) -> str:
    """The fence block: what this landing admits from this lane, and what it will not.

    The admitted list is ``declared + config.lane_scope``, which is exactly the ``held``
    tuple ``loop._scope_block`` compares the diff against — restating the ``## Scope``
    alone would tell a lane its own drop-in and changelog fragment are out of bounds,
    the false positive basicly-kjc5.64 already removed from the gate.

    The route out is the sentinel, not an edit and not a wider declaration: a lane that
    widens its own ``## Scope`` to cover a sibling's path has not resolved the collision,
    it has only moved which gate reports it.
    """
    lines = [
        f"Scope this pass has already handed out. {_admits(repo_root, issue_id)} Every path "
        "below is a still-open sibling lane's declared ground:",
        *(
            f"- {lane} owns {', '.join(f'`{path}`' for path in paths)}"
            for lane, paths in sorted(owned.items())
        ),
        "An edit to one of those is not a shortcut. The landing refuses it before the merge "
        "and holds this record until a human rules on the scope, which costs the pass a "
        "round and the sibling a conflict. When the work genuinely needs one of those "
        "paths, do not edit it and do not widen your own declaration to cover it: write "
        f"{needs_input.SENTINEL_FILE.as_posix()} as "
        '{"fact": "' + SCOPE_FACT + '", "detail": "<the path, the lane that owns it, and '
        'why the work needs it>"} and stop.',
    ]
    return "\n".join(lines)


def _admits(repo_root: Path, issue_id: str) -> str:
    """What this landing admits from this lane, or that its scope check is inert.

    An undeclared lane is not a narrowly-scoped one: ``loop._scope_block`` returns early
    on an empty declaration, so claiming the two derived paths were "the paths admitted"
    would invent a fence the gate does not hold and read as a refusal that cannot happen.

    The derived paths are stated as outranking a sibling's glob because they do — the
    gate holds the diff against ``held`` first, so a path inside it never reaches the
    collision check. Sibling scopes here routinely include ``changelog.d/*.md``, and
    without the clause the fence tells every lane not to write its own release note.
    """
    declared = merge.declared_scopes(repo_root, (issue_id,)).get(issue_id, ())
    derived = ", ".join(f"`{path}`" for path in lane_scope(issue_id))
    precedence = (
        f"{derived} are yours by construction and outrank any sibling glob below that "
        "also covers them"
    )
    if not declared:
        return (
            "You declare no `## Scope`, so the landing's own scope check is inert on your "
            f"diff and admits it whole; {precedence}."
        )
    admitted = ", ".join(f"`{path}`" for path in declared)
    return f"The paths this landing admits from you: {admitted}. Also {precedence}."
