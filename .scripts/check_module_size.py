"""Fail when a tracked Python module crosses the agent working-set token cap.

Nothing in this stack measures module size (basicly-u2hl.5): ruff has no module-length
rule, so `cli.py` reached 53,095 tokens with every gate green. This is that measurement,
wired as a `[[verify.checks]]` fast entry.

**Tokens, not lines.** Lines drift with docstring and comment density. Tokens are the unit
the sizing governor already runs in, so :data:`~basicly.read_cost.SCOPE_FILE_READ_CAP` and
``read_cost._text_tokens`` are *imported* rather than respelled here — a second chars/4
spelling is a number that can drift from the one the loop actually budgets with.

**The cap is that constant, and the reason it is that constant matters.** 4,000 tokens is
where `decompose`'s own comment says "the whole-file band ends": above it an agent stops
reading a file whole and starts reading selectively. So this is an **agent working-set
gate** and must not be read as a defect-density claim — the defect literature argues the
other way (Hatton 1997 found mid-size components best; Koru 2008 found smaller modules
proportionally *more* defect-prone). See `docs/requirements/factory-loop.md` §9.3.

**A ratchet, not a hard cap**, and `ratchet.py` holds that mechanism for all three gates:
78 of 179 tracked modules were already over the cap when this landed, so each one's go-live
token count is recorded in `[tool.module_size.frozen]` and a frozen module may only
*shrink*. Adding a line to `cli.py` is therefore a failing commit until something else in
it goes — with one exception below. A module may exceed the cap deliberately with a column-0
``module-size-waiver:`` comment, against a counted `waiver_count`; `waivers.py` holds what
that comment has to say and `check_waivers.py` censuses both markers.

**Top-level imports are not counted** — :func:`module_tokens` holds the measurement that
forced that, and the frozen baselines were recomputed once on the same measure, so nothing
is forgiven except the import block.

Tests are in scope and are the larger half of the debt (42 of the 78 frozen entries, and
the worst single offender) — they will not fall out of a `src/` refactor as a side effect.

Run::

    uv run python .scripts/check_module_size.py
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from ratchet import (  # noqa: E402 - the path above comes first
    Finding,
    Ratchet,
    RatchetError,
    compose_ratchet,
    count_delta_remedy,
    frozen_table,
    rebaseline_clause,
    report,
    stale,
    tracked_sources,
)
from waivers import (  # noqa: E402 - the path above comes first
    COHESION,
    COST,
    Waiver,
    read_waiver,
    unclassified_waiver,
    waiver_findings,
)

from basicly.read_cost import SCOPE_FILE_READ_CAP, _text_tokens  # noqa: E402  (path set above)

# The gate, as `[tool.module_size]` and `[ratchet.module_size]` spell it, and the table a
# `_graduated` finding tells a reviewer to delete an entry from.
_GATE = "module_size"
FROZEN_TABLE = frozen_table(_GATE)

# The waiver marker, without its colon. Spelled as a string rather than a column-0 comment so
# that this script and its tests can name it without waiving themselves. Public because
# `check_waivers.py` censuses both gates' markers and must not respell either.
WAIVER_MARKER = "module-size-waiver"

LABEL = "module-size"

# Where "first touch brings it under" stops applying (OQ-12, resolved 2026-08-08). Below
# 2x the cap a single extraction reaches 4,000; above it, not growing is the whole rule.
_BRING_UNDER_MULTIPLE = 2

# A top-level import, and the continuation of a parenthesised one. Column 0 only: an
# import deferred inside a function is code the reader pays for, and the handful this
# repo defers on purpose each carry a PLC0415 suppression saying why.
_IMPORT_LINE = re.compile(r"^(?:import|from)\s")


@dataclass(frozen=True)
class Module:
    """One tracked module: its repo-relative path, its size, and its waiver if it has one."""

    path: str
    tokens: int
    waiver: Waiver | None = None


def module_tokens(text: str) -> int:
    """*text*'s size in tokens, counting everything except its top-level imports.

    The imports are excluded because counting them made the ratchet punish the one
    change it exists to encourage. Measured 2026-08-08: extracting ``contention`` out of
    ``supervise.py`` — a real split, along a named responsibility, that took 48,020
    tokens off a frozen module — forced one ``from . import contention`` line into
    ``cli.py``, and that four-token line failed ``cli.py``'s own ratchet. So splitting a
    large module required shrinking every module that imports it, and the cheapest way
    to satisfy the gate was to not split anything.

    Excluding them is the narrowest fix that removes the perverse incentive without
    weakening the ratchet: an import is one line, it is not what makes a file too large
    to read whole, and code growth is still measured to the token. A module that grew by
    real content still fails — only the import block is free.

    Returns:
        The count with top-level ``import``/``from`` statements, and the continuation lines
        of a parenthesised ``from ... import (`` block, removed.
    """
    kept: list[str] = []
    depth = 0
    for line in text.splitlines(keepends=True):
        if depth:
            depth += line.count("(") - line.count(")")
            continue
        if _IMPORT_LINE.match(line):
            depth = line.count("(") - line.count(")")
            continue
        kept.append(line)
    return _text_tokens("".join(kept))


def load_ratchet(repo: Path) -> Ratchet[int]:
    """This gate's baseline: token counts, and a waiver count that is a count of modules."""
    return compose_ratchet(repo, _GATE, count_key="waiver_count", entry_type=int)


def tracked_modules(repo: Path) -> list[Module]:
    """Every tracked ``.py`` in scope, measured, ordered by path.

    Raises:
        RatchetError: git refused to list the tree.
    """
    modules = [
        Module(
            path=name,
            tokens=module_tokens(text),
            waiver=read_waiver(name, text, WAIVER_MARKER),
        )
        for name, text in tracked_sources(repo)
    ]
    return sorted(modules, key=lambda module: module.path)


def _over_cap(module: Module, cap: int) -> Finding:
    """A module the closed frozen list does not cover has crossed the cap."""
    return Finding(
        subject=module.path,
        detail=f"{module.tokens} tokens, over the {cap}-token cap",
        remedy=(
            "split it along a nameable responsibility (not into _part1/_part2), or waive it "
            f"with a column-0 `# {WAIVER_MARKER}: {COHESION}|{COST}(<record-id>): <reason>` "
            f"and {count_delta_remedy(_GATE, 1)}"
        ),
    )


def _grew(module: Module, baseline: int, cap: int) -> Finding:
    """A frozen module went the wrong way."""
    return Finding(
        subject=module.path,
        detail=(
            f"{module.tokens} tokens, up from the frozen {baseline}; a module over the "
            f"{cap}-token cap may only shrink"
        ),
        remedy=_shrink_remedy(baseline, cap),
    )


def _shrink_remedy(baseline: int, cap: int) -> str:
    """How far this module has to come down, which depends on how far over it is (OQ-12).

    Under 2x the cap one extraction reaches it, so the rule is payable by whoever touched
    the module. At or above 2x the obligation is only not to grow: bringing a 13x module
    under the cap is a decomposition track of its own, and charging it as a toll on the
    next edit is what stopped a lint adoption dead on 2026-08-08.
    """
    if baseline < cap * _BRING_UNDER_MULTIPLE:
        return f"bring it under {cap} tokens — one extraction reaches it from {baseline}"
    return (
        f"bring it back under {baseline} tokens; reaching {cap} from here is a "
        "decomposition track of its own, not this change's obligation"
    )


def _graduated(module: Module, baseline: int, cap: int) -> Finding:
    """A frozen module reached the cap, so its licence to be large has expired."""
    return Finding(
        subject=module.path,
        detail=(
            f"{module.tokens} tokens is within the {cap}-token cap, but it is still frozen "
            f"at {baseline}, which licenses it to grow back"
        ),
        remedy=f'delete `"{module.path}"` from {FROZEN_TABLE}',
    )


def _module_finding(module: Module, ratchet: Ratchet[int], cap: int) -> Finding | None:
    """How one module disagrees with the ratchet, or ``None`` if it agrees."""
    if module.waiver is not None:
        return unclassified_waiver(WAIVER_MARKER, module.waiver) if not module.waiver.kind else None
    baseline = ratchet.frozen.get(module.path)
    if baseline is None:
        return _over_cap(module, cap) if module.tokens > cap else None
    if module.tokens > baseline:
        return _grew(module, baseline, cap)
    if module.tokens <= cap:
        return _graduated(module, baseline, cap)
    return None


def collect(
    modules: Iterable[Module], ratchet: Ratchet[int], cap: int = SCOPE_FILE_READ_CAP
) -> list[Finding]:
    """Every disagreement between the tree and the recorded ratchet.

    Args:
        modules: The measured tracked modules.
        ratchet: The recorded baseline.
        cap: The token cap; defaults to the sizing governor's own constant.

    Returns:
        The findings, ordered by subject then detail.
    """
    modules = list(modules)
    present = {module.path: module for module in modules}
    waived = {module.path for module in modules if module.waiver is not None}

    findings = [
        finding
        for module in modules
        if (finding := _module_finding(module, ratchet, cap)) is not None
    ]
    for path in sorted(ratchet.frozen):
        if path in waived:
            findings.append(
                stale(_GATE, path, "it carries a waiver, which replaces the frozen entry")
            )
        elif path not in present:
            findings.append(stale(_GATE, path, "no readable tracked module is at this path"))
    findings.extend(waiver_findings(_GATE, waived, ratchet.count))
    return sorted(findings, key=lambda finding: (finding.subject, finding.detail))


def main() -> int:
    """Entry point: report every module that crossed the cap or grew past its baseline."""
    try:
        ratchet = load_ratchet(REPO_ROOT)
        modules = tracked_modules(REPO_ROOT)
    except RatchetError as exc:
        print(f"{LABEL}: {exc}", file=sys.stderr)
        return 1

    findings = collect(modules, ratchet)
    if findings:
        report(LABEL, findings)
        return 1
    waived = sum(1 for module in modules if module.waiver is not None)
    print(
        f"{LABEL}: {len(modules)} tracked modules within the {SCOPE_FILE_READ_CAP}-token cap "
        f"or their frozen baseline ({len(ratchet.frozen)} frozen, {waived} waived"
        f"{rebaseline_clause(ratchet)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
