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

_GATE = "module_size"
FROZEN_TABLE = frozen_table(_GATE)

WAIVER_MARKER = "module-size-waiver"

LABEL = "module-size"

_BRING_UNDER_MULTIPLE = 2

_IMPORT_LINE = re.compile(r"^(?:import|from)\s")


@dataclass(frozen=True)
class Module:
    path: str
    tokens: int
    waiver: Waiver | None = None


def module_tokens(text: str) -> int:

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
    return compose_ratchet(repo, _GATE, count_key="waiver_count", entry_type=int)


def tracked_modules(repo: Path) -> list[Module]:

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
    return Finding(
        subject=module.path,
        detail=(
            f"{module.tokens} tokens, up from the frozen {baseline}; a module over the "
            f"{cap}-token cap may only shrink"
        ),
        remedy=_shrink_remedy(baseline, cap),
    )


def _shrink_remedy(baseline: int, cap: int) -> str:

    if baseline < cap * _BRING_UNDER_MULTIPLE:
        return f"bring it under {cap} tokens — one extraction reaches it from {baseline}"
    return (
        f"bring it back under {baseline} tokens; reaching {cap} from here is a "
        "decomposition track of its own, not this change's obligation"
    )


def _graduated(module: Module, baseline: int, cap: int) -> Finding:
    return Finding(
        subject=module.path,
        detail=(
            f"{module.tokens} tokens is within the {cap}-token cap, but it is still frozen "
            f"at {baseline}, which licenses it to grow back"
        ),
        remedy=f'delete `"{module.path}"` from {FROZEN_TABLE}',
    )


def _module_finding(module: Module, ratchet: Ratchet[int], cap: int) -> Finding | None:
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
