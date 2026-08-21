"""Fail when a tracked Python module carries more prose than the ratchet allows.

Prose is comments plus docstrings, as a share of the module's tokens. Both, because
`convention = "google"` makes docstrings mandatory on public API (D101/D102/D103/D105
fire without them), so a gate on `#` comments alone just moves the narration into a
docstring the linter already demands.

Pragmas are not prose: `# noqa`, `# nosec`, `# type: ignore` and the waiver markers are
read by tools, and charging for them would price a suppression the same as an essay.

The cap is 50%. Measured 2026-08-12 over 280 in-scope modules: median 36.3%, p75 51.2%.
So 50% refuses the worst quartile from growing and refuses anything else from joining it,
at 75 frozen entries — the size the `module-size` ratchet already carries.

A ratchet, not a hard cap — `ratchet.py` holds that mechanism for all three gates: a module
over 50% records its go-live share in `[tool.comment_density.frozen]` and may only fall.
Deleting a true comment to pass is the gaming shape `python-guidelines` names; the repair is
to cut narration, not evidence.

Run::

    uv run python .scripts/check_comment_density.py
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
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
    waiver_findings,
    waiver_reason,
)

from basicly.read_cost import _text_tokens  # noqa: E402  (path set above)

# The gate, as `[tool.comment_density]` and `[ratchet.comment_density]` spell it, and the
# table a `_graduated` finding tells a reviewer to delete an entry from.
_GATE = "comment_density"
FROZEN_TABLE = frozen_table(_GATE)

CAP = 50.0

# Below this a single mandatory docstring dominates the file, so the share says nothing
# about how densely anyone wrote. 7 modules are exempt on this floor, all stubs.
MIN_TOKENS = 200

_PRAGMA = re.compile(
    r"#\s*(?:noqa|nosec|type:\s*ignore|pragma:|pyright:|mypy:|ruff:|[\w-]+-waiver:)"
)
# The waiver marker, without its colon. A string rather than a column-0 comment, so this
# script and its tests can name it without waiving themselves.
_WAIVER_MARKER = "comment-density-waiver"

_DOCSTRING_OWNER = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_LABEL = "comment-density"


@dataclass(frozen=True)
class Module:
    """One tracked module: its path, its prose share, and its waiver if it has one."""

    path: str
    share: float
    tokens: int
    waiver: str | None = None


def prose_tokens(text: str) -> int:
    """*text*'s comment and docstring tokens, excluding pragmas.

    Args:
        text: A whole module's source, not a slice of one.

    Returns:
        The token count.

    Raises:
        RatchetError: *text* does not parse, so it has no share to state. Returning 0 was
            the most dangerous available answer: the two size ratchets pull opposite ways,
            an extraction is safe only when the extracted unit is prose-*heavier* than the
            module it leaves, and a fragment lifted out of its class reads as pure code.
            Measured 2026-08-20, a lane derived 66% by a second path against this 0
            (basicly-e7rtjn).
    """
    try:
        comments = [
            token.string
            for token in tokenize.generate_tokens(io.StringIO(text).readline)
            if token.type == tokenize.COMMENT and not _PRAGMA.search(token.string)
        ]
        tree = ast.parse(text)
    except (SyntaxError, tokenize.TokenError, ValueError) as err:
        raise RatchetError(
            "cannot state a prose share for source that does not parse; measure an "
            "extracted unit as the module it will become, not as a raw slice"
        ) from err
    docstrings = [
        doc
        for node in ast.walk(tree)
        if isinstance(node, _DOCSTRING_OWNER) and (doc := ast.get_docstring(node, clean=False))
    ]
    return sum(_text_tokens(part) for part in (*comments, *docstrings))


def measure(text: str) -> tuple[float, int]:
    """*text*'s prose share as a percentage to one decimal, and its total tokens."""
    total = _text_tokens(text)
    if not total:
        return 0.0, 0
    return round(100 * prose_tokens(text) / total, 1), total


def load_ratchet(repo: Path) -> Ratchet[float]:
    """This gate's baseline: prose shares, and a waiver count that is a count of modules."""
    return compose_ratchet(repo, _GATE, count_key="waiver_count", entry_type=float)


def tracked_modules(repo: Path) -> list[Module]:
    """Every tracked ``.py`` in scope at or above :data:`MIN_TOKENS`, measured, by path.

    Raises:
        RatchetError: git refused to list the tree.
    """
    modules = []
    for name, text in tracked_sources(repo):
        try:
            share, tokens = measure(text)
        except RatchetError:
            # A tracked module that does not parse is ruff's finding to report, not a
            # reason this gate cannot run. The refusal is for the other caller: a lane
            # measuring a fragment, which has no ruff run standing behind it.
            share, tokens = 0.0, _text_tokens(text)
        if tokens >= MIN_TOKENS:
            modules.append(
                Module(
                    path=name,
                    share=share,
                    tokens=tokens,
                    waiver=waiver_reason(text, _WAIVER_MARKER),
                )
            )
    return sorted(modules, key=lambda module: module.path)


def _over_cap(module: Module) -> Finding:
    """A module the closed frozen list does not cover has crossed the cap."""
    return Finding(
        subject=module.path,
        detail=f"{module.share}% prose, over the {CAP}% cap",
        remedy=(
            "cut narration — a comment that restates the statement below it, or a docstring "
            "that describes the code rather than the contract — or waive it with a column-0 "
            f"`# {_WAIVER_MARKER}: <reason>` and {count_delta_remedy(_GATE, 1)}"
        ),
    )


def _grew(module: Module, baseline: float) -> Finding:
    """A frozen module went the wrong way."""
    return Finding(
        subject=module.path,
        detail=(
            f"{module.share}% prose, up from the frozen {baseline}%; a module over the "
            f"{CAP}% cap may only fall"
        ),
        remedy=(
            f"bring it back under {baseline}%. Cut narration, not evidence: a comment "
            "recording a measurement, a vendor fact or why a branch exists is the reason "
            "this gate is a ratchet and not a purge"
        ),
    )


def _graduated(module: Module, baseline: float) -> Finding:
    """A frozen module reached the cap, so its licence to be prose-heavy has expired."""
    return Finding(
        subject=module.path,
        detail=(
            f"{module.share}% prose is within the {CAP}% cap, but it is still frozen at "
            f"{baseline}%, which licenses it to grow back"
        ),
        remedy=f'delete `"{module.path}"` from {FROZEN_TABLE}',
    )


def _module_finding(module: Module, ratchet: Ratchet[float]) -> Finding | None:
    """How one module disagrees with the ratchet, or ``None`` if it agrees."""
    if module.waiver is not None:
        return None
    baseline = ratchet.frozen.get(module.path)
    if baseline is None:
        return _over_cap(module) if module.share > CAP else None
    if module.share > baseline:
        return _grew(module, baseline)
    if module.share <= CAP:
        return _graduated(module, baseline)
    return None


def collect(modules: Iterable[Module], ratchet: Ratchet[float]) -> list[Finding]:
    """Every disagreement between the tree and the recorded ratchet.

    Args:
        modules: The measured tracked modules.
        ratchet: The recorded baseline.

    Returns:
        The findings, ordered by subject then detail.
    """
    modules = list(modules)
    present = {module.path for module in modules}
    waived = {module.path for module in modules if module.waiver is not None}

    findings = [
        finding for module in modules if (finding := _module_finding(module, ratchet)) is not None
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
    """Entry point: report every module over the cap or above its frozen share."""
    try:
        ratchet = load_ratchet(REPO_ROOT)
        modules = tracked_modules(REPO_ROOT)
    except RatchetError as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1

    findings = collect(modules, ratchet)
    if findings:
        report(_LABEL, findings)
        return 1
    waived = sum(1 for module in modules if module.waiver is not None)
    print(
        f"{_LABEL}: {len(modules)} tracked modules within the {CAP}% cap or their frozen "
        f"share ({len(ratchet.frozen)} frozen, {waived} waived{rebaseline_clause(ratchet)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
