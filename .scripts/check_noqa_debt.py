from __future__ import annotations

import io
import re
import sys
import tokenize
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from ratchet import (  # noqa: E402 - the path above comes first
    MAY_ONLY_TRACK,
    Finding,
    Ratchet,
    RatchetError,
    compose_ratchet,
    count_delta_remedy,
    fragment,
    report,
    tracked_sources,
)

_GATE = "noqa_debt"
RATCHET_TABLE = f"[tool.{_GATE}]"
FROZEN_FRAGMENT = fragment(f"{_GATE}.frozen")

BLANKET = "blanket"

_MARKER = re.compile(r"#[ \t]*noqa", re.IGNORECASE)
_CODE = re.compile(r"[A-Z]+[0-9]+")
_SEPARATOR = re.compile(r"[,\s]+")

_REASON_LEAD = " \t-:\N{EN DASH}\N{EM DASH}"

_LABEL = "noqa-debt"


@dataclass(frozen=True)
class Suppression:
    path: str
    line: int
    code: str
    reason: str | None = None

    @property
    def site(self) -> str:
        return f"{self.path}:{self.line}"


def _codes(body: str) -> tuple[list[str], str]:
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
    for match in _MARKER.finditer(comment):
        rest = comment[match.end() :].lstrip(" \t")
        if not rest:
            yield BLANKET, None
            continue
        if not rest.startswith(":"):
            continue
        codes, trailing = _codes(rest[1:])
        reason = trailing.strip().strip(_REASON_LEAD).strip() or None
        for code in codes:
            yield code, reason


def suppressions(path: str, text: str) -> list[Suppression]:

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


def load_ratchet(repo: Path) -> Ratchet[int]:

    return compose_ratchet(
        repo, _GATE, count_key="unreasoned_count", entry_type=int, may_only=MAY_ONLY_TRACK
    )


def tracked_suppressions(repo: Path) -> list[Suppression]:

    found: list[Suppression] = []
    for name, text in sorted(tracked_sources(repo)):
        found.extend(suppressions(name, text))
    return found


def _unlisted(code: str, count: int) -> Finding:
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


def _unreasoned(sites: list[str], ratchet: Ratchet[int]) -> list[Finding]:

    if len(sites) == ratchet.count:
        return []
    grew = len(sites) > ratchet.count
    repair = count_delta_remedy(_GATE, len(sites) - ratchet.count)
    return [
        Finding(
            subject="pyproject.toml",
            detail=(
                f"{len(sites)} suppression(s) carry no reason but unreasoned_count is "
                f"{ratchet.count} — one was {'added' if grew else 'justified'} "
                f"without saying so (at: {', '.join(sites) or 'none'})"
            ),
            remedy=(
                f"write the reason as `# noqa: CODE - reason`, or {repair}" if grew else repair
            ),
        )
    ]


def collect(found: Iterable[Suppression], ratchet: Ratchet[int]) -> list[Finding]:

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


def main() -> int:
    try:
        ratchet = load_ratchet(REPO_ROOT)
        found = tracked_suppressions(REPO_ROOT)
    except RatchetError as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1

    findings = collect(found, ratchet)
    if findings:
        report(_LABEL, findings)
        return 1
    print(
        f"{_LABEL}: {len(found)} suppressions across {len(ratchet.frozen)} codes, each at its "
        f"frozen count ({ratchet.count} carrying no reason)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
