from __future__ import annotations

import subprocess  # nosec B404
import sys
import tomllib
import types
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from basicly.dropin import (  # noqa: E402 - the path above comes first
    COUNT_DELTA,
    FRAGMENT_DIR,
    MAY_ONLY_FALL,
    MAY_ONLY_TRACK,
    RATCHET_SECTION,
    FragmentError,
    compose,
)

SCOPE_ROOTS = ("src", "tests", ".scripts", ".basicly/core")

_PLACES = 1


__all__ = ["MAY_ONLY_FALL", "MAY_ONLY_TRACK"]


class RatchetError(Exception):
    pass


@dataclass(frozen=True)
class Ratchet[Number: (int, float)]:
    frozen: Mapping[str, Number]
    count: int
    rebaselined: Mapping[str, tuple[str, ...]] = types.MappingProxyType({})


@dataclass(frozen=True)
class Finding:
    subject: str
    detail: str
    remedy: str


def frozen_table(gate: str) -> str:
    return f"[tool.{gate}.frozen]"


def fragment(gate: str) -> str:
    return f"[{RATCHET_SECTION}.{gate}] in {FRAGMENT_DIR}/<bead-id>.toml"


def rebaseline_clause(ratchet: Ratchet) -> str:

    declared = sum(len(names) for names in ratchet.rebaselined.values())
    entries = len(ratchet.rebaselined)
    if not entries:
        return ""
    if declared == entries:
        return f", {entries} rebaselined"
    return f", {declared} rebaselined across {entries} entr{'y' if entries == 1 else 'ies'}"


def count_delta_remedy(gate: str, moved: int) -> str:

    return f"record `{COUNT_DELTA} = {moved:+d}` under {fragment(gate)}"


def compose_ratchet[Number: (int, float)](
    repo: Path,
    gate: str,
    *,
    count_key: str,
    entry_type: type[Number],
    may_only: str = MAY_ONLY_FALL,
) -> Ratchet[Number]:

    fractional = entry_type is float
    table = _table(repo, gate)
    frozen = table.get("frozen", {})
    count = table.get(count_key)
    permitted = int | float if fractional else int
    if not isinstance(frozen, dict) or not all(
        isinstance(value, permitted) for value in frozen.values()
    ):
        raise RatchetError(f"{frozen_table(gate)} must map each subject to its go-live number")
    if not isinstance(count, int):
        raise RatchetError(f"[tool.{gate}] must declare {count_key} as an integer")
    try:
        composed = compose(
            repo,
            gate,
            frozen={subject: entry_type(value) for subject, value in frozen.items()},
            count=count,
            fractional=fractional,
            may_only=may_only,
        )
    except FragmentError as exc:
        raise RatchetError(str(exc)) from exc
    return Ratchet(
        frozen={
            subject: entry_type(round(value, _PLACES)) for subject, value in composed.frozen.items()
        },
        count=composed.count,
        rebaselined=composed.rebaselined,
    )


def _table(repo: Path, gate: str) -> dict:
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RatchetError(f"could not read pyproject.toml: {exc}") from exc
    table = data.get("tool", {}).get(gate)
    if not isinstance(table, dict):
        raise RatchetError(f"no [tool.{gate}] in pyproject.toml")
    return table


def tracked_sources(repo: Path) -> Iterator[tuple[str, str]]:

    completed = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(repo), "ls-files", "-z", "--", *SCOPE_ROOTS],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"git exited {completed.returncode}"
        raise RatchetError(f"could not list tracked files: {detail}")
    for name in completed.stdout.split("\0"):
        if not name.endswith(".py"):
            continue
        try:
            text = (repo / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        yield name, text


def stale(gate: str, subject: str, detail: str) -> Finding:
    return Finding(
        subject=subject,
        detail=detail,
        remedy=f'delete `"{subject}"` from {frozen_table(gate)}',
    )


def report(label: str, findings: Iterable[Finding]) -> None:
    for finding in findings:
        print(f"{label}: {finding.subject}: {finding.detail}", file=sys.stderr)
        print(f"{label}:   {finding.remedy}", file=sys.stderr)
