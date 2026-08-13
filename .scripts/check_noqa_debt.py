"""Fail when the `# noqa` suppression debt grows without saying so (basicly-u2hl.12).

Nothing counts suppressions. `docs/requirements/factory-loop.md` §9.1 recorded the
debt as 46 across 20 files; re-measured 2026-08-08 after the `S`/`BLE` families landed it is
**76 across 13 codes**, and every one of those arrived through a green gate. A suppression is
the one edit that makes a linter quieter, so the only thing that can police it is a count
kept outside the code — this is that count, wired as a `[[verify.checks]]` fast entry beside
`module-size`.

**A ratchet, not a ban.** Each code's go-live count is recorded in `[tool.noqa_debt.frozen]`
and the tree must agree with it exactly. Four ways it can disagree, each its own finding:

* A code's count **rose**: a suppression was added. Legitimate ones are added by raising the
  number in the same diff, which is a line in `pyproject.toml` a reviewer reads.
* A code is **absent from the table**: this rule has never been suppressed here. Refused by
  default, because "we already suppress that one" is the argument this gate exists to make
  someone write down.
* A code's count **fell** and the table did not: the record still licenses the higher number,
  which is the regrowth licence `check_module_size.py` names as this repo's fail-open shape.
  An entry that reaches zero is deleted, not zeroed.
* A suppression carries **no reason**, against the house form `# noqa: CODE - reason`. Seven
  do; `unreasoned_count` ratchets them in both directions exactly as `[tool.module_size]`'s
  `waiver_count` does, so one can neither appear nor be quietly swapped for another.

**Read the way ruff reads, not the way grep does.** Measured against ruff 0.14 on 2026-08-08:
`# NOQA:`, `#noqa:F841`, `# noqa : F841` and a directive trailing another comment
(`# type: ignore  # noqa: F841`) all suppress; `# noqa/nosec pair:` does **not** — ruff calls
it an invalid directive and warns. Counting by substring would therefore have counted a
comment that suppresses nothing, and `src/basicly/br.py:70` is exactly that comment, sitting
in this tree today. Two consequences:

* Comments come from :mod:`tokenize`, so a marker inside a string or a docstring is a
  mention, not a suppression — which is what lets this gate and its tests spell the marker
  without counting themselves, and is the discriminator a regex over raw text cannot draw.
* An invalid directive is ignored here as ruff ignores it. It is a real defect and it is
  deliberately not this gate's: refusing it would fail the committed tree, and the repair
  lives in a file outside this change.

A **blanket** `# noqa` (no codes) is counted under the pseudo-code :data:`BLANKET`, which the
table does not record, so it is refused by the absent-from-the-table finding. Ruff cannot do
this itself: `RUF100` only catches a blanket directive that suppresses *nothing*, and one
that suppresses everything on its line passes.

Scope is every tracked ``.py`` under :data:`SCOPE_ROOTS`, matching `check_module_size.py`.

Run::

    uv run python .scripts/check_noqa_debt.py
"""

from __future__ import annotations

import io
import re
import subprocess  # nosec B404
import sys
import tokenize
import tomllib
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
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

# Every directory whose Python this repo authors, as `check_module_size.py` scopes it: the
# kit and the hooks ship to consumers, so a suppression there has the widest blast radius.
SCOPE_ROOTS = ("src", "tests", ".scripts", ".basicly/core")

# Where the ratchet is recorded, how a failure names it, and where a lane records a change to
# it instead of editing those shared tables (basicly-ef7t).
RATCHET_TABLE = "[tool.noqa_debt]"
FROZEN_TABLE = "[tool.noqa_debt.frozen]"
FRAGMENT = f"[{RATCHET_SECTION}.noqa_debt] in {FRAGMENT_DIR}/<bead-id>.toml"
FROZEN_FRAGMENT = f"[{RATCHET_SECTION}.noqa_debt.frozen] in {FRAGMENT_DIR}/<bead-id>.toml"

# What a codeless directive is counted as. Lowercase, so it can never collide with a ruff
# code, and absent from the frozen table, so it fails on sight.
BLANKET = "blanket"

# The directive, as ruff reads it. `#` then optional horizontal space then `noqa`; anything
# other than end-of-comment or `:` after it is an invalid directive ruff only warns about.
_MARKER = re.compile(r"#[ \t]*noqa", re.IGNORECASE)
_CODE = re.compile(r"[A-Z]+[0-9]+")
_SEPARATOR = re.compile(r"[,\s]+")

# Punctuation the house form puts between the code and its reason; stripped so that a dash
# alone does not read as a justification. Named escapes rather than the characters: `RUF001`
# reads either dash as an ambiguous character, and here they are the data, not the prose.
# This tree writes the em dash; the en dash is admitted because the difference is invisible.
_REASON_LEAD = " \t-:\N{EN DASH}\N{EM DASH}"

_LABEL = "noqa-debt"


class RatchetError(Exception):
    """The gate could not reach an answer: no ratchet to read, or a file it cannot parse."""


@dataclass(frozen=True)
class Suppression:
    """One suppressed code at one place, with the reason it carries if it carries one."""

    path: str
    line: int
    code: str
    reason: str | None = None

    @property
    def site(self) -> str:
        """Where a reader has to go to act on it."""
        return f"{self.path}:{self.line}"


@dataclass(frozen=True)
class Ratchet:
    """The recorded state a change is measured against."""

    frozen: Mapping[str, int]
    unreasoned_count: int


@dataclass(frozen=True)
class Finding:
    """One way the tree disagrees with the ratchet, with the repair named."""

    subject: str
    detail: str
    remedy: str


def _codes(body: str) -> tuple[list[str], str]:
    """The codes at the head of *body*, and whatever follows them."""
    codes: list[str] = []
    pos = len(body) - len(body.lstrip(" \t"))
    while (match := _CODE.match(body, pos)) is not None:
        codes.append(match.group())
        pos = match.end()
        gap = _SEPARATOR.match(body, pos)
        if gap is None:
            break
        pos = gap.end()
    return codes, body[pos:]


def _directives(comment: str) -> Iterator[tuple[str, str | None]]:
    """Each ``(code, reason)`` one comment suppresses, ruff's reading of it."""
    for match in _MARKER.finditer(comment):
        rest = comment[match.end() :].lstrip(" \t")
        if not rest:
            yield BLANKET, None
            continue
        if not rest.startswith(":"):
            continue  # ruff calls this an invalid directive and suppresses nothing.
        codes, trailing = _codes(rest[1:])
        reason = trailing.strip().strip(_REASON_LEAD).strip() or None
        for code in codes:
            yield code, reason


def suppressions(path: str, text: str) -> list[Suppression]:
    """Every suppression *text* declares, read from its comments only.

    Args:
        path: The repo-relative path, carried onto each finding so a reader can go there.
        text: The module's source.

    Returns:
        One :class:`Suppression` per code per directive, in source order.

    Raises:
        RatchetError: The module does not tokenize, so its comments cannot be found. The
            gate reports that rather than skipping the file, which would exempt it.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (SyntaxError, tokenize.TokenError) as exc:
        raise RatchetError(f"could not tokenize {path}: {exc}") from exc
    return [
        Suppression(path=path, line=token.start[0], code=code, reason=reason)
        for token in tokens
        if token.type == tokenize.COMMENT
        for code, reason in _directives(token.string)
    ]


def load_ratchet(repo: Path) -> Ratchet:
    """The baseline in ``pyproject.toml``, with the ``basicly.d`` fragments applied.

    Raises:
        RatchetError: The table is absent or malformed — the gate must not pass by
            defaulting to an empty baseline, which would report the whole debt as new.
    """
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RatchetError(f"could not read pyproject.toml: {exc}") from exc
    table = data.get("tool", {}).get("noqa_debt")
    if not isinstance(table, dict):
        raise RatchetError(f"no {RATCHET_TABLE} in pyproject.toml")
    frozen = table.get("frozen", {})
    count = table.get("unreasoned_count")
    if not isinstance(frozen, dict) or not all(isinstance(v, int) for v in frozen.values()):
        raise RatchetError(f"{FROZEN_TABLE} must map each rule code to its go-live count")
    if not isinstance(count, int):
        raise RatchetError(f"{RATCHET_TABLE} must declare unreasoned_count as an integer")
    baseline = compose(repo, "noqa_debt", frozen=frozen, count=count)
    return Ratchet(frozen=baseline.frozen, unreasoned_count=baseline.count)


def tracked_suppressions(repo: Path) -> list[Suppression]:
    """Every suppression in every tracked ``.py`` under :data:`SCOPE_ROOTS`.

    Args:
        repo: The repository root.

    Returns:
        The suppressions, ordered by path then line. A tracked path with no readable file —
        deleted in the working tree — contributes nothing rather than failing the run.

    Raises:
        RatchetError: git refused to list the tree, or a listed module does not tokenize.
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
    found: list[Suppression] = []
    for name in sorted(completed.stdout.split("\0")):
        if not name.endswith(".py"):
            continue
        try:
            text = (repo / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.extend(suppressions(name, text))
    return found


def _unlisted(code: str, count: int) -> Finding:
    """A code the table does not record — including a codeless blanket directive."""
    if code == BLANKET:
        return Finding(
            subject=BLANKET,
            detail=(
                f"{count} codeless suppression(s), each silencing every rule on its line; "
                f"{RATCHET_TABLE} records none and never will"
            ),
            remedy="name the codes it needs — ruff passes a blanket that suppresses anything",
        )
    return Finding(
        subject=code,
        detail=(
            f"{count} suppression(s) of {code}, which {RATCHET_TABLE} does not record; this "
            "rule has never been suppressed in this tree"
        ),
        remedy=f"remove it, or record `{code} = {count:+d}` in {FROZEN_FRAGMENT}",
    )


def _rose(code: str, count: int, baseline: int) -> Finding:
    """The debt grew: a suppression was added without announcing it."""
    return Finding(
        subject=code,
        detail=f"{count} suppressions of {code}, up from the frozen {baseline}",
        remedy=(
            f"fix what it silences, or give it a `# noqa: {code} - reason` naming the "
            f"alternative rejected and record `{code} = {count - baseline:+d}` in "
            f"{FROZEN_FRAGMENT}"
        ),
    )


def _fell(code: str, count: int, baseline: int) -> Finding:
    """The debt fell and the record did not, so it still licenses the old number."""
    return Finding(
        subject=code,
        detail=(
            f"{count} suppressions of {code}, down from the frozen {baseline}; the record "
            f"still licenses {baseline}"
        ),
        remedy=(
            f"record `{code} = {count - baseline:+d}` in {FROZEN_FRAGMENT} — a debt that fell "
            "has to be banked or it grows back for free"
        ),
    )


def _unreasoned(sites: list[str], ratchet: Ratchet) -> list[Finding]:
    """The reason ratchet, which moves only in a diff that says it moved.

    A count rather than a list, matching `[tool.module_size]`'s `waiver_count`: the point is
    that an unargued suppression cannot appear, and cannot be swapped for another one, in a
    diff that does not say so.
    """
    if len(sites) == ratchet.unreasoned_count:
        return []
    grew = len(sites) > ratchet.unreasoned_count
    moved = len(sites) - ratchet.unreasoned_count
    repair = f"record `{COUNT_DELTA} = {moved:+d}` in {FRAGMENT}"
    return [
        Finding(
            subject="pyproject.toml",
            detail=(
                f"{len(sites)} suppression(s) carry no reason but unreasoned_count is "
                f"{ratchet.unreasoned_count} — one was {'added' if grew else 'justified'} "
                f"without saying so (at: {', '.join(sites) or 'none'})"
            ),
            remedy=(
                f"write the reason as `# noqa: CODE - reason`, or {repair}" if grew else repair
            ),
        )
    ]


def collect(found: Iterable[Suppression], ratchet: Ratchet) -> list[Finding]:
    """Every disagreement between the tree and the recorded ratchet.

    Args:
        found: The measured suppressions.
        ratchet: The recorded baseline.

    Returns:
        The findings, ordered by subject then detail.
    """
    found = list(found)
    counts = Counter(item.code for item in found)
    findings: list[Finding] = []
    for code in sorted(set(counts) | set(ratchet.frozen)):
        count = counts.get(code, 0)
        baseline = ratchet.frozen.get(code)
        if baseline is None:
            findings.append(_unlisted(code, count))
        elif count > baseline:
            findings.append(_rose(code, count, baseline))
        elif count < baseline:
            findings.append(_fell(code, count, baseline))
    sites = sorted(item.site for item in found if item.reason is None and item.code != BLANKET)
    findings.extend(_unreasoned(sites, ratchet))
    return sorted(findings, key=lambda finding: (finding.subject, finding.detail))


def report(findings: Iterable[Finding]) -> None:
    """Print each finding as the disagreement, then how to repair it."""
    for finding in findings:
        print(f"{_LABEL}: {finding.subject}: {finding.detail}", file=sys.stderr)
        print(f"{_LABEL}:   {finding.remedy}", file=sys.stderr)


def main() -> int:
    """Entry point: report every code whose suppression count left its recorded baseline."""
    try:
        ratchet = load_ratchet(REPO_ROOT)
        found = tracked_suppressions(REPO_ROOT)
    except (RatchetError, FragmentError) as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1

    findings = collect(found, ratchet)
    if findings:
        report(findings)
        return 1
    print(
        f"{_LABEL}: {len(found)} suppressions across {len(ratchet.frozen)} codes, each at its "
        f"frozen count ({ratchet.unreasoned_count} carrying no reason)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
