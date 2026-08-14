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

from . import merge


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
