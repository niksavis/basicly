"""The codebase-improvement loop: one sensor reading, one target, one lane per run.

The second loop shape (basicly-u2hl.27), after ``humanlayer/skills``'
``design-control-loop`` (MIT). The delivery loop takes a requirement and ships a
change; this one drives a **property of the codebase** toward a target on a
schedule. Three of the five parts already existed and are used here rather than
restated -- only the controller and the actuator are new:

============  ==================================================================
set point     ``read_cost.SCOPE_FILE_READ_CAP``, the size above which an agent
              stops reading a file whole -- imported, never respelled here
sensor        ``check_module_size``, imported, so the loop and the commit gate
              measure with one implementation and cannot disagree
dampener      the frozen ratchet in ``[tool.module_size]``, which stops the
              property getting worse while the loop chips at it
controller    :func:`candidates` and the admission below -- this module
actuator      :func:`dispatch` -- this module
============  ==================================================================

**The engine disposes, agents propose.** Selection is arithmetic over the sensor's
output: the unwaived module furthest above the set point, ties broken by path. No
model chooses the target, so two runs over one tree select the same one.

**The standing debt produces no findings.** ``check_module_size.collect`` reports a
module that *grew* past its baseline; a frozen module sitting quietly at 51,078
tokens is exactly what the dampener permits, and exactly what this loop exists to
reduce. So the controller reads the sensor's *measurements*, never its findings --
a loop driven by the gate's failures would have nothing to do on a green tree.

**Coverage is reported.** One run selects one target out of many and prints the
count it dropped. A silent top-1 reads as "nothing else is over the cap", which is
the false-negative shape this repo keeps paying for.

**One open lane.** A scheduled loop that outruns review produces conflicting and
duplicate work, so the loop refuses to select a second target while a lane it filed
is unlanded (:func:`open_lanes`).

Run on a schedule::

    basicly loop improve --dry-run   # select and print; file nothing
    basicly loop improve             # select and file one lane
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path[:0] = [str(SCRIPTS_DIR), str(REPO_ROOT / "src")]

import check_module_size as sensor  # noqa: E402 - the paths above come first

from basicly import br, plan_record, policy, supervise, wip  # noqa: E402 - path set above
from basicly.read_cost import SCOPE_FILE_READ_CAP  # noqa: E402 - path set above

# The label every lane this loop files carries. It *is* the loop's flow-control
# state: the bound reads it back off the tracker, so a lane dispatched by one run is
# visible to the next without this script keeping a record of its own.
LANE_LABEL = "improvement-loop"

# The bound, in lanes. One, deliberately: the property being driven is not urgent,
# and review capacity is the quantity that actually runs out.
MAX_OPEN_LANES = 1

# What a filed lane declares. `L2` and 70000 are the figures basicly-u2hl.23 -- the
# last single-module lane this repo planned by hand -- was planned at. They are
# declared, not measured, and a lane is free to re-plan itself.
LANE_TYPE = "task"
LANE_INTEGRITY = "L2"
LANE_BUDGET_TOKENS = 70000

# The status a landed lane carries. Everything else `br list --label` returns is
# work in progress, whatever phase it derived to.
LANDED_STATUS = "closed"

_LABEL = "improve"


@dataclass(frozen=True)
class Candidate:
    """One module above the set point: the error the loop would close, and by how much."""

    path: str
    tokens: int
    excess: int

    @property
    def issue_id(self) -> str:
        """What a refusal names this candidate by, satisfying :class:`wip.Unit`.

        The bound counts units carrying a ``br`` id and a candidate has none until
        the actuator files one, so the target path stands in for it.
        """
        return self.path


def candidates(
    modules: list[sensor.Module], cap: int = SCOPE_FILE_READ_CAP
) -> tuple[Candidate, ...]:
    """Every module above *cap*, worst error first -- the controller's whole judgment.

    Ordered by excess descending, then by path, so the selection is re-derivable from
    the tree rather than merely repeatable: a loop whose target depended on iteration
    order could not be audited after the fact.

    A **waived** module is not a candidate. Its ``module-size-waiver:`` comment is a
    recorded decision that it is cohesive at that size, and a loop that dispatched a
    lane against it would re-litigate that decision on every run.
    """
    ranked = [
        Candidate(path=module.path, tokens=module.tokens, excess=module.tokens - cap)
        for module in modules
        if module.waiver is None and module.tokens > cap
    ]
    return tuple(sorted(ranked, key=lambda candidate: (-candidate.excess, candidate.path)))


def open_lanes(repo_root: Path) -> tuple[str, ...]:
    """The lanes this loop has filed that have not landed, by id.

    "Unlanded" here is "not closed", which is deliberately **wider** than
    ``wip.DOWNSTREAM_PHASES``. BUILD's bound counts work that has already merged and
    awaits review; this one must also count a lane that is still being built, because
    a second target selected over the same tree is exactly the duplicate work the
    bound exists to prevent. So membership is read off the label, not off a phase.
    """
    try:
        selection = supervise.lane_selection(repo_root, LANE_LABEL)
    except supervise.LaneSelectionError:
        # No bead carries the label at all: the loop has never filed one. That is an
        # empty bound, not an error -- the raise is worded for a mistyped selector.
        return ()
    return tuple(sorted(issue for issue, status in selection if status != LANDED_STATUS))


def admit(open_ids: tuple[str, ...], ranked: tuple[Candidate, ...]) -> wip.WipAdmission[Candidate]:
    """Split the ranked candidates into the lane the bound starts and the ones it holds.

    :class:`wip.WipAdmission` is basicly-u2hl.23's record, reused rather than restated:
    ``limit``/``downstream``/``admitted``/``refused`` and the ``stalled`` predicate mean
    here what they mean at BUILD's dispatch, so the two bounds stay one shape.

    What is *not* reused is ``wip.admit`` and the messages it words for BUILD: it
    filters its input to the phases downstream of build and reads the limit from
    ``[policy] max_downstream_wip``, and neither is this loop's question -- see
    :func:`open_lanes` for the phase half and :data:`MAX_OPEN_LANES` for the limit.
    """
    headroom = max(0, MAX_OPEN_LANES - len(open_ids))
    return wip.WipAdmission(
        limit=MAX_OPEN_LANES,
        downstream=open_ids,
        admitted=tuple(ranked[:headroom]),
        refused=tuple(ranked[headroom:]),
    )


def lane_title(target: Candidate) -> str:
    """The filed lane's title: what it is for, in the sensor's own units."""
    return f"Bring {target.path} under the {SCOPE_FILE_READ_CAP}-token module size cap"


def lane_scope(target: Candidate) -> tuple[str, ...]:
    """The globs a filed lane may touch, narrow enough to run beside another lane.

    Four entries, and each earns its place: the module itself; the siblings an
    extraction creates, named after their origin so the lane's own output stays inside
    the scope it declared; the module's tests, which move with the code it holds; and
    ``pyproject.toml``, where the frozen entry that must expire lives.
    """
    path = Path(target.path)
    return (
        target.path,
        f"{path.parent.as_posix()}/{path.stem}_*.py",
        f"tests/test_{path.stem}*.py",
        "pyproject.toml",
    )


def lane_body(target: Candidate, dropped: int) -> str:
    """The bead body a filed lane carries: the DoR sections, the scope, and the plan.

    Built through :func:`policy.compose_body`, so a lane this loop files carries the
    same required sections as one a decomposition files, and the preamble records the
    measurement that selected it -- a target with no number on it cannot be checked.
    """
    demonstration = (
        f"`uv run python .scripts/check_module_size.py` prints its pass line with no "
        f"`{target.path}` entry in `[tool.module_size.frozen]`"
    )
    return policy.compose_body(
        LANE_TYPE,
        {
            plan_record.ACCEPTANCE_HEADING: (
                f"- When `.scripts/check_module_size.py` measures `{target.path}` it shall "
                f"report at most {SCOPE_FILE_READ_CAP} tokens and the module shall carry no "
                f"`[tool.module_size.frozen]` entry - check: "
                f"`uv run python .scripts/check_module_size.py` passes after that entry is "
                f"deleted"
            ),
            plan_record.SCOPE_HEADING: "\n".join(f"- `{glob}`" for glob in lane_scope(target)),
            plan_record.PLAN_HEADING: plan_record.render_plan_section(
                (), LANE_BUDGET_TOKENS, LANE_INTEGRITY, demonstration
            ),
        },
        preamble=(
            f"Selected by the improvement loop (`.scripts/improvement_controller.py`) as the "
            f"module furthest above the {SCOPE_FILE_READ_CAP}-token set point: "
            f"{target.tokens} tokens, {target.excess} over. "
            f"{dropped} other candidate(s) were above the cap and not selected this run."
        ),
    )


def dispatch(repo_root: Path, target: Candidate, dropped: int) -> str:
    """File one lane for *target* and return its ``br`` id.

    The actuator writes to the tracker and nowhere else: the lane it files is picked
    up by the dispatcher the delivery loop already has, so this loop never spawns an
    agent itself and inherits every bound that path already carries.
    """
    proc = br.run_br(
        repo_root,
        [
            "create",
            lane_title(target),
            "-t",
            LANE_TYPE,
            "-l",
            LANE_LABEL,
            "-d",
            lane_body(target, dropped),
            "--json",
        ],
    )
    return str(json.loads(proc.stdout)["id"])


def _sensor_lines(modules: list[sensor.Module], ranked: tuple[Candidate, ...]) -> list[str]:
    """What the sensor read and what the controller made of it, including the drop."""
    waived = sum(1 for module in modules if module.waiver is not None)
    return [
        f"set point: {SCOPE_FILE_READ_CAP} tokens (read_cost.SCOPE_FILE_READ_CAP)",
        f"sensor:    .scripts/check_module_size.py measured {len(modules)} tracked module(s)",
        f"error:     {len(ranked)} above the set point, worst first; {waived} waived, "
        f"which this loop does not re-litigate",
    ]


def _selection_lines(
    ranked: tuple[Candidate, ...], admission: wip.WipAdmission[Candidate]
) -> list[str]:
    """The bound's verdict and, when it admits one, the target and what was dropped."""
    lanes = f"lanes:     {len(admission.downstream)}/{admission.limit} unlanded lane(s)"
    if admission.downstream:
        lanes += f": {', '.join(admission.downstream)}"
    lines = [lanes]
    if admission.stalled:
        lines.append(
            f"no-op:     bound to {admission.limit} open lane; land "
            f"{', '.join(admission.downstream)} before the next run selects another"
        )
        return lines
    target = admission.admitted[0]
    lines.append(
        f"selected:  {target.path} - {target.tokens} tokens, {target.excess} over the cap "
        f"(1 of {len(ranked)})"
    )
    lines.append(
        f"dropped:   {len(ranked) - 1} candidate(s) not selected; the next run re-ranks them"
    )
    return lines


def _say(lines: list[str]) -> None:
    """Print the pass's narrative, one labelled line at a time."""
    for line in lines:
        print(f"{_LABEL}: {line}")


def main(argv: list[str] | None = None) -> int:
    """One run of the loop: measure, select one target, dispatch at most one lane."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="select and print, but file no lane")
    args = parser.parse_args(argv)

    try:
        modules = sensor.tracked_modules(REPO_ROOT)
    except sensor.RatchetError as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1

    ranked = candidates(modules)
    lines = _sensor_lines(modules, ranked)
    if not ranked:
        _say([*lines, "done:      every tracked module is at the set point"])
        return 0

    admission = admit(open_lanes(REPO_ROOT), ranked)
    lines += _selection_lines(ranked, admission)
    if admission.stalled:
        _say(lines)
        return 0
    target = admission.admitted[0]
    if args.dry_run:
        _say([*lines, f"dry run:   no lane filed for {target.path}"])
        return 0
    issue_id = dispatch(REPO_ROOT, target, len(ranked) - 1)
    _say([*lines, f"dispatch:  {issue_id} filed, labelled {LANE_LABEL}"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
