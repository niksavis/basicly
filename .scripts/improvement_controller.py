from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path[:0] = [str(SCRIPTS_DIR), str(REPO_ROOT / "src")]

import check_module_size as sensor  # noqa: E402 - the paths above come first

from basicly import config, plan_record, policy, supervise, tracker, wip  # noqa: E402 - path above
from basicly.read_cost import SCOPE_FILE_READ_CAP  # noqa: E402 - path set above

LANE_LABEL = "improvement-loop"

MAX_OPEN_LANES = 1

LANE_TYPE = "task"
LANE_INTEGRITY = "L2"
LANE_BUDGET_TOKENS = 70000

LANDED_STATUS = "closed"

_LABEL = "improve"


@dataclass(frozen=True)
class Candidate:
    path: str
    tokens: int
    excess: int

    @property
    def issue_id(self) -> str:

        return self.path


def candidates(
    modules: list[sensor.Module], cap: int = SCOPE_FILE_READ_CAP
) -> tuple[Candidate, ...]:

    ranked = [
        Candidate(path=module.path, tokens=module.tokens, excess=module.tokens - cap)
        for module in modules
        if module.waiver is None and module.tokens > cap
    ]
    return tuple(sorted(ranked, key=lambda candidate: (-candidate.excess, candidate.path)))


def open_lanes(repo_root: Path) -> tuple[str, ...]:

    try:
        selection = supervise.lane_selection(repo_root, LANE_LABEL)
    except supervise.LaneSelectionError:
        return ()
    return tuple(sorted(issue for issue, status in selection if status != LANDED_STATUS))


def admit(open_ids: tuple[str, ...], ranked: tuple[Candidate, ...]) -> wip.WipAdmission[Candidate]:

    headroom = max(0, MAX_OPEN_LANES - len(open_ids))
    return wip.WipAdmission(
        limit=MAX_OPEN_LANES,
        downstream=open_ids,
        admitted=tuple(ranked[:headroom]),
        refused=tuple(ranked[headroom:]),
    )


def lane_title(target: Candidate) -> str:
    return f"Bring {target.path} under the {SCOPE_FILE_READ_CAP}-token module size cap"


def split_pattern(target: Candidate) -> str:

    path = Path(target.path)
    return f"{path.parent.as_posix()}/{path.stem}_*.py"


def resolves(repo_root: Path, glob: str) -> bool:
    return next(repo_root.glob(glob), None) is not None


def lane_scope(repo_root: Path, target: Candidate) -> tuple[str, ...]:

    derived = (f"tests/test_{Path(target.path).stem}*.py", "pyproject.toml")
    return (
        target.path,
        split_pattern(target),
        *(glob for glob in derived if resolves(repo_root, glob)),
    )


def lane_body(repo_root: Path, target: Candidate, dropped: int) -> str:

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
            plan_record.SCOPE_HEADING: "\n".join(
                f"- `{glob}`" for glob in lane_scope(repo_root, target)
            ),
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

    print(f"tracker:   mode {config.load_tracker_mode(repo_root)}")
    return tracker.create_record(
        repo_root,
        [
            "create",
            lane_title(target),
            "-t",
            LANE_TYPE,
            "-l",
            LANE_LABEL,
            "-d",
            lane_body(repo_root, target, dropped),
            "--json",
        ],
    )


def _sensor_lines(modules: list[sensor.Module], ranked: tuple[Candidate, ...]) -> list[str]:
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
    for line in lines:
        print(f"{_LABEL}: {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="The codebase-improvement loop: one reading, one target, one lane per run."
    )
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
        scope = ", ".join(lane_scope(REPO_ROOT, target))
        _say([*lines, f"scope:     {scope}", f"dry run:   no lane filed for {target.path}"])
        return 0
    issue_id = dispatch(REPO_ROOT, target, len(ranked) - 1)
    _say([*lines, f"dispatch:  {issue_id} filed, labelled {LANE_LABEL}"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
