from __future__ import annotations

import subprocess  # nosec B404
import sys
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PACKAGE_ROOT = "src/basicly"
TEST_ROOT = "tests"

TEST_PREFIX = "test_"
TEST_SUFFIX = ".py"

_LABEL = "test-naming"

_PACKAGE_INIT = "__init__.py"


class ScanError(Exception):
    pass


@dataclass(frozen=True)
class Finding:
    unit: str
    detail: str
    remedy: str


def _tracked(repo: Path, pathspec: str) -> list[str]:

    completed = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(repo), "ls-files", "-z", "--", pathspec],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"git exited {completed.returncode}"
        raise ScanError(f"could not list tracked files under {pathspec}: {detail}")
    return [name for name in completed.stdout.split("\0") if name]


def source_units(paths: Iterable[str]) -> list[str]:

    units = set()
    for path in paths:
        prefix = f"{PACKAGE_ROOT}/"
        if not path.startswith(prefix) or not path.endswith(TEST_SUFFIX):
            continue
        head, _, tail = path[len(prefix) :].partition("/")
        if not tail and head == _PACKAGE_INIT:
            continue
        units.add(head.removesuffix(TEST_SUFFIX) if not tail else head)
    return sorted(units)


def test_stems(paths: Iterable[str]) -> set[str]:

    stems = set()
    for path in paths:
        if not path.startswith(f"{TEST_ROOT}/"):
            continue
        name = path.rpartition("/")[2]
        if name.startswith(TEST_PREFIX) and name.endswith(TEST_SUFFIX):
            stems.add(name[len(TEST_PREFIX) : -len(TEST_SUFFIX)])
    return stems


def covering_stem(unit: str, stems: Collection[str], units: Collection[str]) -> str | None:

    if unit in stems:
        return unit
    derived = sorted(stem for stem in stems if stem.startswith(f"{unit}_") and stem not in units)
    return derived[0] if derived else None


def collect(units: Iterable[str], stems: Collection[str]) -> list[Finding]:

    units = list(units)
    return [
        Finding(
            unit=unit,
            detail="no test file named after it (§9.4)",
            remedy=(
                f"move its tests into {TEST_ROOT}/{TEST_PREFIX}{unit}{TEST_SUFFIX}, or into "
                f"{TEST_ROOT}/{TEST_PREFIX}{unit}_<aspect>{TEST_SUFFIX} when they justify a "
                "split — leaving them in the file named after the module it was extracted "
                "from is the drift this gate exists to stop"
            ),
        )
        for unit in sorted(units)
        if covering_stem(unit, stems, units) is None
    ]


def report(findings: Iterable[Finding]) -> None:
    for finding in findings:
        print(f"{_LABEL}: {PACKAGE_ROOT}/{finding.unit}: {finding.detail}", file=sys.stderr)
        print(f"{_LABEL}:   {finding.remedy}", file=sys.stderr)


def scan(repo: Path) -> tuple[list[str], set[str]]:

    units = source_units(_tracked(repo, PACKAGE_ROOT))
    stems = test_stems(_tracked(repo, TEST_ROOT))
    if not units:
        raise ScanError(f"no source units found under {PACKAGE_ROOT}")
    if not stems:
        raise ScanError(f"no {TEST_PREFIX}*{TEST_SUFFIX} files found under {TEST_ROOT}")
    return units, stems


def main() -> int:
    try:
        units, stems = scan(REPO_ROOT)
    except ScanError as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1

    findings = collect(units, stems)
    if findings:
        report(findings)
        return 1
    print(f"{_LABEL}: {len(units)} source units each have a test file named after them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
