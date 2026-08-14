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

A ratchet, not a hard cap: a module over 50% records its go-live share in
`[tool.comment_density.frozen]` and may only fall. Deleting a true comment to pass is the
gaming shape `python-guidelines` names; the repair is to cut narration, not evidence.

Run::

    uv run python .scripts/check_comment_density.py
"""

from __future__ import annotations

import ast
import io
import re
import subprocess  # nosec B404
import sys
import tokenize
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
from basicly.read_cost import _text_tokens  # noqa: E402  (path set above)

SCOPE_ROOTS = ("src", "tests", ".scripts", ".basicly/core")

# Where the ratchet is recorded, how a failure names it, and where a lane records a change
# to the count instead of editing that shared table (basicly-05g0, applying basicly-ef7t).
RATCHET_TABLE = "[tool.comment_density]"
FROZEN_TABLE = "[tool.comment_density.frozen]"
FRAGMENT = f"[{RATCHET_SECTION}.comment_density] in {FRAGMENT_DIR}/<bead-id>.toml"

CAP = 50.0

# Below this a single mandatory docstring dominates the file, so the share says nothing
# about how densely anyone wrote. 7 modules are exempt on this floor, all stubs.
_MIN_TOKENS = 200

_PRAGMA = re.compile(
    r"#\s*(?:noqa|nosec|type:\s*ignore|pragma:|pyright:|mypy:|ruff:|[\w-]+-waiver:)"
)
_WAIVER = re.compile(r"^#[ \t]*comment-density-waiver:[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)

_DOCSTRING_OWNER = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_LABEL = "comment-density"


class RatchetError(Exception):
    """The gate could not reach an answer: no ratchet to read, or git refused the question."""


@dataclass(frozen=True)
class Module:
    """One tracked module: its path, its prose share, and its waiver if it has one."""

    path: str
    share: float
    tokens: int
    waiver: str | None = None


@dataclass(frozen=True)
class Ratchet:
    """The recorded state a change is measured against."""

    frozen: Mapping[str, float]
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


def prose_tokens(text: str) -> int:
    """*text*'s comment and docstring tokens, excluding pragmas.

    Args:
        text: The module's source.

    Returns:
        The token count, or 0 if the source does not parse.
    """
    try:
        comments = [
            token.string
            for token in tokenize.generate_tokens(io.StringIO(text).readline)
            if token.type == tokenize.COMMENT and not _PRAGMA.search(token.string)
        ]
        tree = ast.parse(text)
    except SyntaxError, tokenize.TokenError, ValueError:
        return 0
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


def load_ratchet(repo: Path) -> Ratchet:
    """The baseline in ``pyproject.toml``, with the ``basicly.d`` fragments applied.

    Args:
        repo: The repository root.

    Returns:
        The composed frozen prose shares and the composed waiver count.

    Raises:
        RatchetError: The table is absent or malformed. The gate must not pass by
            defaulting to an empty baseline, which would fail every frozen module at once.
        FragmentError: A fragment declares a delta that is not a number.
    """
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RatchetError(f"could not read pyproject.toml: {exc}") from exc
    table = data.get("tool", {}).get("comment_density")
    if not isinstance(table, dict):
        raise RatchetError(f"no {RATCHET_TABLE} in pyproject.toml")
    frozen = table.get("frozen", {})
    count = table.get("waiver_count")
    if not isinstance(frozen, dict) or not all(
        isinstance(value, int | float) for value in frozen.values()
    ):
        raise RatchetError(f"{FROZEN_TABLE} must map each path to its go-live prose share")
    if not isinstance(count, int):
        raise RatchetError(f"{RATCHET_TABLE} must declare waiver_count as an integer")
    baseline = compose(
        repo,
        "comment_density",
        frozen={path: float(v) for path, v in frozen.items()},
        count=count,
        fractional=True,
    )
    # Rounded to the precision `measure` reports, because a composed share is a sum of
    # one-decimal floats and binary addition does not stay on that grid: a lane cutting
    # 0.1 off the 51.3 frozen for `fsck.py` composes 51.199999999999996, and the module it
    # just cut, measured at 51.2, would be reported as having grown.
    return Ratchet(
        frozen={path: round(share, 1) for path, share in baseline.frozen.items()},
        waiver_count=baseline.count,
    )


def tracked_modules(repo: Path) -> list[Module]:
    """Every tracked ``.py`` under :data:`SCOPE_ROOTS`, measured.

    Args:
        repo: The repository root.

    Returns:
        The modules at or above :data:`_MIN_TOKENS`, ordered by path.

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
        share, tokens = measure(text)
        if tokens >= _MIN_TOKENS:
            modules.append(
                Module(path=name, share=share, tokens=tokens, waiver=waiver_reason(text))
            )
    return sorted(modules, key=lambda module: module.path)


def _over_cap(module: Module) -> Finding:
    """A module the closed frozen list does not cover has crossed the cap."""
    return Finding(
        path=module.path,
        detail=f"{module.share}% prose, over the {CAP}% cap",
        remedy=(
            "cut narration — a comment that restates the statement below it, or a docstring "
            "that describes the code rather than the contract — or waive it with a column-0 "
            f"`# comment-density-waiver: <reason>` and record `{COUNT_DELTA} = 1` under "
            f"{FRAGMENT}"
        ),
    )


def _grew(module: Module, baseline: float) -> Finding:
    """A frozen module went the wrong way."""
    return Finding(
        path=module.path,
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
        path=module.path,
        detail=(
            f"{module.share}% prose is within the {CAP}% cap, but it is still frozen at "
            f"{baseline}%, which licenses it to grow back"
        ),
        remedy=f'delete `"{module.path}"` from {FROZEN_TABLE}',
    )


def _stale(path: str, detail: str) -> Finding:
    """A frozen entry that no longer describes anything."""
    return Finding(path=path, detail=detail, remedy=f'delete `"{path}"` from {FROZEN_TABLE}')


def _module_finding(module: Module, ratchet: Ratchet) -> Finding | None:
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


def _waiver_findings(waived: Collection[str], ratchet: Ratchet) -> list[Finding]:
    """The waiver-count ratchet, which moves only in a diff that says it moved."""
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


def collect(modules: Iterable[Module], ratchet: Ratchet) -> list[Finding]:
    """Every disagreement between the tree and the recorded ratchet.

    Args:
        modules: The measured tracked modules.
        ratchet: The recorded baseline.

    Returns:
        The findings, ordered by path then detail.
    """
    modules = list(modules)
    present = {module.path for module in modules}
    waived = {module.path for module in modules if module.waiver is not None}

    findings = [
        finding for module in modules if (finding := _module_finding(module, ratchet)) is not None
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
    """Entry point: report every module over the cap or above its frozen share."""
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
        f"{_LABEL}: {len(modules)} tracked modules within the {CAP}% cap or their frozen "
        f"share ({len(ratchet.frozen)} frozen, {waived} waived)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
