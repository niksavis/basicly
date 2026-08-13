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

**A ratchet, not a hard cap.** 78 of 179 tracked modules were already over the cap when
this landed, and failing all of them would have meant turning the gate off. Instead each
one's go-live token count is recorded in `[tool.module_size.frozen]`, and a frozen module
may only *shrink*. Three consequences, each its own finding:

* A module not in the list may never cross the cap. The list is closed — an entry is only
  ever removed.
* A frozen module that grew fails, naming both counts. Adding a line to `cli.py` is
  therefore a failing commit until something else in it goes — with one exception below.
* A frozen module that has fallen to the cap has graduated, and its entry must go with it.
  Leaving the entry would license regrowth back to the go-live number, which is the
  fail-open shape this repo keeps paying for.

**Top-level imports are not counted** — :func:`module_tokens` holds the measurement that
forced that, and the frozen baselines were recomputed once on the same measure, so nothing
is forgiven except the import block.

**Waivers, and why they are counted.** A genuinely cohesive module may exceed the cap
deliberately by carrying a one-line reason as a column-0 comment: the marker is
``module-size-waiver:`` followed by the reason. The count is itself ratcheted against
`waiver_count` — exactly as `[tool.vulture]`'s suppression list is policed by
`wired_or_deleted.py` — so a waiver may be added only in a diff that moves the count, which
a lane does with a `count_delta` in its own fragment (basicly-ef7t). The reason must be
non-empty and the marker must start the line, which is what keeps a mention of it inside a
string or a docstring from waiving the file that mentions it.

Scope is every tracked ``.py`` under :data:`SCOPE_ROOTS`. Tracked, from `git ls-files`,
because an untracked scratch file is not something a gate should have an opinion about.
Tests are in scope and are the larger half of the debt (42 of the 78 frozen entries, and
the worst single offender) — they will not fall out of a `src/` refactor as a side effect.

Run::

    uv run python .scripts/check_module_size.py
"""

from __future__ import annotations

import re
import subprocess  # nosec B404
import sys
import tomllib
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from basicly.dropin import (  # noqa: E402 - the path above comes first
    COUNT_DELTA,
    FRAGMENT_DIR,
    RATCHET_SECTION,
    FragmentError,
    compose,
)
from basicly.read_cost import SCOPE_FILE_READ_CAP, _text_tokens  # noqa: E402  (path set above)

# Every directory whose Python this repo authors. `.basicly/core` is here because the kit
# and the hooks ship to consumers and run in the dispatch path; omitting it would exempt
# the code with the widest blast radius.
SCOPE_ROOTS = ("src", "tests", ".scripts", ".basicly/core")

# Where the ratchet is recorded, how a failure names it, and where a lane records a change to
# the count instead of editing that shared table (basicly-ef7t).
RATCHET_TABLE = "[tool.module_size]"
FROZEN_TABLE = "[tool.module_size.frozen]"
FRAGMENT = f"[{RATCHET_SECTION}.module_size] in {FRAGMENT_DIR}/<bead-id>.toml"

# A waiver, as a module may spell one. Column-0 comment, non-empty reason: a marker quoted
# inside a string or indented in a docstring is a mention, not a waiver, so this script and
# its tests can name the marker without waiving themselves.
_WAIVER = re.compile(r"^#[ \t]*module-size-waiver:[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)

_LABEL = "module-size"

# Where "first touch brings it under" stops applying (OQ-12, resolved 2026-08-08). Below
# 2x the cap a single extraction reaches 4,000; above it, not growing is the whole rule.
_BRING_UNDER_MULTIPLE = 2

# A top-level import, and the continuation of a parenthesised one. Column 0 only: an
# import deferred inside a function is code the reader pays for, and the handful this
# repo defers on purpose each carry a PLC0415 suppression saying why.
_IMPORT_LINE = re.compile(r"^(?:import|from)\s")


class RatchetError(Exception):
    """The gate could not reach an answer: no ratchet to read, or git refused the question."""


@dataclass(frozen=True)
class Module:
    """One tracked module: its repo-relative path, its size, and its waiver if it has one."""

    path: str
    tokens: int
    waiver: str | None = None


@dataclass(frozen=True)
class Ratchet:
    """The recorded state a change is measured against."""

    frozen: Mapping[str, int]
    waiver_count: int


@dataclass(frozen=True)
class Finding:
    """One way the tree disagrees with the ratchet, with the repair named."""

    path: str
    detail: str
    remedy: str


def waiver_reason(text: str) -> str | None:
    """The reason a module waives the cap with, or ``None`` if it does not waive it."""
    match = _WAIVER.search(text)
    return match.group(1) if match else None


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


def load_ratchet(repo: Path) -> Ratchet:
    """The baseline in ``pyproject.toml``, with the ``basicly.d`` fragments applied.

    Raises:
        RatchetError: The table is absent or malformed — the gate must not pass by
            defaulting to an empty baseline, which would fail every frozen module at once.
    """
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RatchetError(f"could not read pyproject.toml: {exc}") from exc
    table = data.get("tool", {}).get("module_size")
    if not isinstance(table, dict):
        raise RatchetError(f"no {RATCHET_TABLE} in pyproject.toml")
    frozen = table.get("frozen", {})
    count = table.get("waiver_count")
    if not isinstance(frozen, dict) or not all(isinstance(v, int) for v in frozen.values()):
        raise RatchetError(f"{FROZEN_TABLE} must map each path to its go-live token count")
    if not isinstance(count, int):
        raise RatchetError(f"{RATCHET_TABLE} must declare waiver_count as an integer")
    baseline = compose(repo, "module_size", frozen=frozen, count=count)
    return Ratchet(frozen=baseline.frozen, waiver_count=baseline.count)


def tracked_modules(repo: Path) -> list[Module]:
    """Every tracked ``.py`` under :data:`SCOPE_ROOTS`, measured.

    Returns:
        The modules, ordered by path. A tracked path with no readable file — deleted in the
        working tree, or unreadable — is skipped; a frozen entry for it is then reported as
        stale rather than silently satisfied.

    Raises:
        RatchetError: git refused to list the tree.
    """
    completed = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(repo), "ls-files", "-z", "--", *SCOPE_ROOTS],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"git exited {completed.returncode}"
        raise RatchetError(f"could not list tracked files: {detail}")
    modules = []
    for name in completed.stdout.split("\0"):
        if not name.endswith(".py"):
            continue
        try:
            text = (repo / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        modules.append(Module(path=name, tokens=module_tokens(text), waiver=waiver_reason(text)))
    return sorted(modules, key=lambda module: module.path)


def _over_cap(module: Module, cap: int) -> Finding:
    """A module the closed frozen list does not cover has crossed the cap."""
    return Finding(
        path=module.path,
        detail=f"{module.tokens} tokens, over the {cap}-token cap",
        remedy=(
            "split it along a nameable responsibility (not into _part1/_part2), or waive it "
            f"with a column-0 `# module-size-waiver: <reason>` and record `{COUNT_DELTA} = 1` "
            f"under {FRAGMENT}"
        ),
    )


def _grew(module: Module, baseline: int, cap: int) -> Finding:
    """A frozen module went the wrong way."""
    return Finding(
        path=module.path,
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
        path=module.path,
        detail=(
            f"{module.tokens} tokens is within the {cap}-token cap, but it is still frozen "
            f"at {baseline}, which licenses it to grow back"
        ),
        remedy=f'delete `"{module.path}"` from {FROZEN_TABLE}',
    )


def _stale(path: str, detail: str) -> Finding:
    """A frozen entry that no longer describes anything."""
    return Finding(path=path, detail=detail, remedy=f'delete `"{path}"` from {FROZEN_TABLE}')


def _module_finding(module: Module, ratchet: Ratchet, cap: int) -> Finding | None:
    """How one module disagrees with the ratchet, or ``None`` if it agrees."""
    if module.waiver is not None:
        return None
    baseline = ratchet.frozen.get(module.path)
    if baseline is None:
        return _over_cap(module, cap) if module.tokens > cap else None
    if module.tokens > baseline:
        return _grew(module, baseline, cap)
    if module.tokens <= cap:
        return _graduated(module, baseline, cap)
    return None


def _waiver_findings(waived: Collection[str], ratchet: Ratchet) -> list[Finding]:
    """The waiver-count ratchet, which moves only in a diff that says it moved.

    The frozen list needs no equivalent, and the asymmetry is the point: an entry added
    there is a line in ``pyproject.toml`` that a reviewer sees, while a waiver is one
    comment somewhere inside a 5,000-line module that nobody would find.
    """
    listed_paths = sorted(waived)
    if len(listed_paths) == ratchet.waiver_count:
        return []
    direction = "added" if len(listed_paths) > ratchet.waiver_count else "removed"
    listed = ", ".join(listed_paths) or "none"
    return [
        Finding(
            path="pyproject.toml",
            detail=(
                f"{len(listed_paths)} module(s) carry a waiver but waiver_count is "
                f"{ratchet.waiver_count} — a waiver was {direction} without saying so "
                f"(waived: {listed})"
            ),
            remedy=(
                f"record `{COUNT_DELTA} = {len(listed_paths) - ratchet.waiver_count:+d}` "
                f"under {FRAGMENT}"
            ),
        )
    ]


def collect(
    modules: Iterable[Module], ratchet: Ratchet, cap: int = SCOPE_FILE_READ_CAP
) -> list[Finding]:
    """Every disagreement between the tree and the recorded ratchet.

    Args:
        modules: The measured tracked modules.
        ratchet: The recorded baseline.
        cap: The token cap; defaults to the sizing governor's own constant.

    Returns:
        The findings, ordered by path then detail.
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
            findings.append(_stale(path, "it carries a waiver, which replaces the frozen entry"))
        elif path not in present:
            findings.append(_stale(path, "no readable tracked module is at this path"))
    findings.extend(_waiver_findings(waived, ratchet))
    return sorted(findings, key=lambda finding: (finding.path, finding.detail))


def report(findings: Iterable[Finding]) -> None:
    """Print each finding as the disagreement, then how to repair it."""
    for finding in findings:
        print(f"{_LABEL}: {finding.path}: {finding.detail}", file=sys.stderr)
        print(f"{_LABEL}:   {finding.remedy}", file=sys.stderr)


def main() -> int:
    """Entry point: report every module that crossed the cap or grew past its baseline."""
    try:
        ratchet = load_ratchet(REPO_ROOT)
        modules = tracked_modules(REPO_ROOT)
    except (RatchetError, FragmentError) as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1

    findings = collect(modules, ratchet)
    if findings:
        report(findings)
        return 1
    waived = sum(1 for module in modules if module.waiver is not None)
    print(
        f"{_LABEL}: {len(modules)} tracked modules within the {SCOPE_FILE_READ_CAP}-token cap "
        f"or their frozen baseline ({len(ratchet.frozen)} frozen, {waived} waived)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
