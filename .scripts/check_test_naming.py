"""Fail when a source module has no test file whose name derives from it.

`docs/requirements/factory-loop.md` §9.4 states the convention — `test_<module>.py`,
or `test_<module>_<aspect>.py` when one module's tests justify a split — and records that it
was *emergent* when it was measured: 48 modules, 84 test files, every module covered. Nothing
made it binding, so the first splits broke it. Measured 2026-08-08 after `dad39f4`: **71 source
units, 10 with no test file named after them**, nine of them created that day. Their tests were
not missing; they stayed in the file named after the module they were extracted from, which is
exactly the drift this gate exists to stop.

**Forward direction only.** A source unit must have a test file; a test file need not have a
source unit. `tests/` legitimately covers `.scripts/`, the git hooks, the shipped kit and
whole-loop integration paths, none of which are modules — failing on those would make the gate
unrunnable rather than stricter.

**The unit is what `basicly` exposes**, not every file on disk: a top-level module is one unit,
and a subpackage is one unit covered by `test_<package>.py` (or a `test_<package>_<aspect>.py`
split). So `renderers/claude.py` is covered by `tests/test_renderers.py`. The limit is stated
rather than hidden: a *new* subpackage with no test file fails, but a new module added inside an
already-covered subpackage does not. Widening it to every nested file is a change to §9.4, not
to this script.

**A derived name that is another unit's own test file does not count** (:func:`covering_stem`).
Without that rule `test_catalog_lint.py` would satisfy `catalog.py` under the derived form, so
splitting `catalog` into `catalog_lint` and deleting `test_catalog.py` would read as covered —
the same "the tests live under the other module's name" shape that produced this gate's own
findings. Measured on this tree: the only two units covered by a derived name are `br`
(`test_br_adapter.py`, `test_br_seam.py`) and `session` (`test_session_overrides.py`), and
neither derived stem names a source unit, so the rule costs nothing today and closes the hole.

Scope is tracked files, from `git ls-files`, for the same reason `check_module_size.py` uses it:
an untracked scratch module is not something a gate should have an opinion about.

Run::

    uv run python .scripts/check_test_naming.py
"""

from __future__ import annotations

import subprocess  # nosec B404
import sys
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The package whose units must be covered, and the tree that covers them. Both are
# repo-relative and slash-separated because that is what `git ls-files` emits on every
# platform — splitting a `Path` here would read differently on Windows.
PACKAGE_ROOT = "src/basicly"
TEST_ROOT = "tests"

# `test_<unit>.py` / `test_<unit>_<aspect>.py`, per §9.4.
TEST_PREFIX = "test_"
TEST_SUFFIX = ".py"

_LABEL = "test-naming"

# Not a unit: a package's `__init__.py` is the package, and the package is already a unit.
_PACKAGE_INIT = "__init__.py"


class ScanError(Exception):
    """The gate could not reach an answer: git refused the question, or the tree is empty.

    Raised rather than returning no findings, because a scan that found nothing to check
    is indistinguishable from a clean tree — the fail-open shape this repo keeps paying for.
    """


@dataclass(frozen=True)
class Finding:
    """One source unit with no test file named after it, with the repair named."""

    unit: str
    detail: str
    remedy: str


def _tracked(repo: Path, pathspec: str) -> list[str]:
    """Every tracked path under *pathspec*, slash-separated and repo-relative.

    Args:
        repo: The repository root.
        pathspec: A repo-relative directory to list.

    Returns:
        The tracked paths, in git's order.

    Raises:
        ScanError: git refused to list the tree.
    """
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
    """The units under :data:`PACKAGE_ROOT` that each need a test file.

    Args:
        paths: Tracked repo-relative paths; anything outside the package is ignored.

    Returns:
        The unit names, sorted and deduplicated. A top-level ``<name>.py`` is the unit
        ``<name>``; anything nested is attributed to the subpackage that holds it, so a
        package contributes one unit however many modules it has.
    """
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
    """What each test file under :data:`TEST_ROOT` claims to cover.

    Args:
        paths: Tracked repo-relative paths; anything outside the test tree is ignored.

    Returns:
        The ``<stem>`` of every ``test_<stem>.py``, at any depth — a test file's *name* is
        the claim §9.4 makes, and which directory it sits in does not change it.
    """
    stems = set()
    for path in paths:
        if not path.startswith(f"{TEST_ROOT}/"):
            continue
        name = path.rpartition("/")[2]
        if name.startswith(TEST_PREFIX) and name.endswith(TEST_SUFFIX):
            stems.add(name[len(TEST_PREFIX) : -len(TEST_SUFFIX)])
    return stems


def covering_stem(unit: str, stems: Collection[str], units: Collection[str]) -> str | None:
    """The stem of the test file covering *unit*, or ``None`` if nothing covers it.

    Args:
        unit: The source unit needing coverage.
        stems: Every available test-file stem.
        units: Every source unit, so a derived candidate that is another unit's own exact
            test file can be rejected — see this module's docstring.

    Returns:
        ``unit`` itself when the exact file exists, else the alphabetically first derived
        stem that does not name another unit, else ``None``.
    """
    if unit in stems:
        return unit
    derived = sorted(stem for stem in stems if stem.startswith(f"{unit}_") and stem not in units)
    return derived[0] if derived else None


def collect(units: Iterable[str], stems: Collection[str]) -> list[Finding]:
    """Every source unit §9.4 leaves uncovered.

    Args:
        units: The source units.
        stems: Every available test-file stem.

    Returns:
        The findings, ordered by unit.
    """
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
    """Print each uncovered unit, then how to repair it."""
    for finding in findings:
        print(f"{_LABEL}: {PACKAGE_ROOT}/{finding.unit}: {finding.detail}", file=sys.stderr)
        print(f"{_LABEL}:   {finding.remedy}", file=sys.stderr)


def scan(repo: Path) -> tuple[list[str], set[str]]:
    """The units to check and the names available to cover them.

    Args:
        repo: The repository root.

    Returns:
        The source units and the test-file stems.

    Raises:
        ScanError: Either side came back empty. Raised rather than returned, because a
            scan that found nothing to check reports no findings — indistinguishable
            from a clean tree, and the fail-open shape this gate exists to remove.
    """
    units = source_units(_tracked(repo, PACKAGE_ROOT))
    stems = test_stems(_tracked(repo, TEST_ROOT))
    if not units:
        raise ScanError(f"no source units found under {PACKAGE_ROOT}")
    if not stems:
        raise ScanError(f"no {TEST_PREFIX}*{TEST_SUFFIX} files found under {TEST_ROOT}")
    return units, stems


def main() -> int:
    """Entry point: report every source unit with no test file named after it."""
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
