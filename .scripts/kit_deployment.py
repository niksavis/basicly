r"""Fail when a host repository does not satisfy the tracker kit's deployment requirements.

Two kit modules each need something from the repository they are installed into that they
cannot do themselves, and until this gate existed each said so **only in a docstring** —
the one place a gate cannot read (basicly-vkh0.21):

``events.py``
    ``events-*.jsonl`` must be declared ``-text`` in ``.gitattributes``. Every ``open()``
    in the kit passes ``newline="\n"``, which controls what *we* write and not what git
    does on checkout; an event id is content-derived, so a byte git rewrote is an id
    changed. Measured on git 2.43.0 with ``core.autocrlf=true``: a host whose only rule is
    ``* text=auto`` returns an LF-only log as CRLF, and the same host carrying ``-text``
    returns it unchanged. This repo's own ``* text=auto eol=lf`` already pinned the
    working-tree ending, so the rule is not repairing live corruption *here* — it is what
    makes byte-exactness a property of the log's rule rather than of a repo-wide ``eol``
    setting, and what carries the requirement to a consumer whose ``*`` rule is bare.
``snapshot.py``
    ``snapshot.jsonl`` and every ``checkpoint-*.jsonl`` are derived — a projection of the
    log that anybody may delete. Committing one recreates the dual-store failure the event
    log exists to escape: two branches each rebuild it, and any record changed on both
    sides is a same-line conflict git cannot union-merge. So the ledger directory's ignore
    rules must cover both patterns.

**The patterns are read off the kit, never spelled a second time here.**
:data:`~snapshot.DERIVED_PATTERNS` and ``events.LOG_GLOB`` are loaded from the host's own
kit and turned into sample paths; drift between the kit and this gate is therefore not
possible, and drift between the kit and the host's rules is exactly what fails. A second
spelling is the defect ``events.py`` already documents as the one this design keeps paying
for — ``snapshot.py`` derives its own names from ``LOG_GLOB`` for the same reason.

**Git answers, not file text.** Both requirements are about what git *does*, so both are
asked of git: ``check-attr`` for the attribute and ``check-ignore`` for the rule. Reading
``.gitattributes`` or ``.gitignore`` and matching globs would reimplement git's precedence
rules — later lines win, a negation may re-include, a nested ignore file may override —
and be wrong in exactly the cases that matter.

Where this stops, stated so it is not mistaken for more:

* It checks the ``text`` attribute, which is the requirement ``events.py`` states. A
  ``filter`` (clean/smudge) or ``working-tree-encoding`` attribute would also rewrite the
  log's bytes and is **not** checked; neither is set anywhere in this tree, and adding one
  to a ledger path would be a deliberate act.
* It asks about sample paths built from the globs, not about files on disk, so it holds
  before a ledger exists — which is the only useful time to answer, since the requirement
  becomes load-bearing the moment the first log is written.
* It is wired as a ``[[verify.checks]]`` entry, so it covers this repo. Unlike
  ``kit-boundary.py`` it is not also a hook: hooks travel to consumers with the catalog
  and ``.scripts/`` does not. ``--repo`` is what makes it portable meanwhile — point it at
  any checkout that has the kit installed and it answers for that checkout.

Run::

    uv run python .scripts/kit_deployment.py
    uv run python .scripts/kit_deployment.py --repo ../some-consumer
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess  # nosec B404
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Where the kit is installed, and where this repo keeps its ledger. Both are host layout
# rather than kit contract — the kit takes its directory as an argument and names no path —
# so they are literals here with `--repo`/`--ledger` to override. `tests/test_kit_deployment.py`
# ties LEDGER_DIR to `tracker_surface.INVENTORY_FILE.parent`, which is the other artifact in
# the same directory, so the two cannot drift apart unnoticed.
KIT_DIR = Path(".basicly") / "core" / "kit" / "tracker"
LEDGER_DIR = Path(".basicly") / "ledger"

# What a `*` is replaced with to turn a kit glob into a path git can answer about. Two
# fills, not one: a host rule that named only `events-0001.jsonl` — the initial log, the
# obvious literal to reach for — would satisfy a single-sample check and then fail on the
# first rotation. Both are legal rotation periods under `snapshot.PERIOD_PATTERN`, so the
# samples are names the ledger really produces rather than arbitrary tokens.
GLOB_FILLS = ("0001", "2026q1")

_SNAPSHOT_MODULE_NAME = "kit_deployment_snapshot"


class DeploymentError(Exception):
    """The gate could not reach an answer: no kit to read, or git refused the question."""


@dataclass(frozen=True)
class Finding:
    """One requirement the host does not satisfy, with the rule it lacks named."""

    path: str
    detail: str
    remedy: str

    @property
    def key(self) -> str:
        """Stable identity for ordering and for a test to assert against."""
        return f"{self.path}:{self.detail}"


def load_kit(kit_dir: Path) -> Any:
    """Load the kit's ``snapshot.py`` by path, the way a consumer without basicly would.

    ``snapshot`` is the one module worth loading: it pulls ``events`` in itself and exposes
    it as ``snapshot.events``, so one load yields both facts this gate needs.

    Args:
        kit_dir: The directory holding the tracker kit's modules.

    Returns:
        The loaded ``snapshot`` module.

    Raises:
        DeploymentError: The kit is not there, or does not import.
    """
    source = kit_dir / "snapshot.py"
    if not source.is_file():
        raise DeploymentError(f"no tracker kit at {kit_dir.as_posix()} — nothing to check")
    spec = importlib.util.spec_from_file_location(_SNAPSHOT_MODULE_NAME, source)
    if spec is None or spec.loader is None:
        raise DeploymentError(f"{source.as_posix()} is not importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_SNAPSHOT_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        raise DeploymentError(f"{source.as_posix()} did not import: {exc}") from exc
    return module


def samples(pattern: str) -> tuple[str, ...]:
    """Every file name a kit glob is checked through.

    A pattern with no ``*`` is its own only sample; one with a ``*`` yields one name per
    entry in :data:`GLOB_FILLS`.
    """
    if "*" not in pattern:
        return (pattern,)
    return tuple(pattern.replace("*", fill) for fill in GLOB_FILLS)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a read-only git query in ``repo``, never raising on a non-zero exit."""
    return subprocess.run(  # nosec B603 B607
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _fatal(completed: subprocess.CompletedProcess[str], question: str) -> None:
    """Turn git's 128 — a bad repository, a bad argument — into this gate's error.

    Raises:
        DeploymentError: git refused the question rather than answering it.
    """
    if completed.returncode >= 128:
        detail = completed.stderr.strip() or f"git exited {completed.returncode}"
        raise DeploymentError(f"git could not answer {question}: {detail}")


def text_attribute(repo: Path, path: str) -> str:
    """What git reports the ``text`` attribute to be for ``path``.

    ``-z`` output rather than the human form: the default is ``<path>: text: <value>`` and
    a path containing ``: `` would be unparseable from it.

    Raises:
        DeploymentError: git refused the question, or answered in a shape this cannot read.
    """
    completed = _git(repo, "check-attr", "-z", "text", "--", path)
    _fatal(completed, f"the text attribute of {path}")
    fields = completed.stdout.split("\0")
    if len(fields) < 3:
        raise DeploymentError(f"git check-attr gave no answer for {path}")
    return fields[2]


def is_ignored_by_a_rule(repo: Path, path: str) -> bool:
    """Whether an ignore rule matches ``path``, regardless of whether it is tracked.

    ``--no-index`` is what separates the two failures: without it git reports a tracked
    file as un-ignored, and the host would be told to add a rule it already has.

    Raises:
        DeploymentError: git refused the question.
    """
    completed = _git(repo, "check-ignore", "-q", "--no-index", "--", path)
    _fatal(completed, f"the ignore rules for {path}")
    return completed.returncode == 0


def is_tracked(repo: Path, path: str) -> bool:
    """Whether ``path`` is in the index — an ignore rule does not un-commit a file."""
    completed = _git(repo, "ls-files", "--error-unmatch", "--", path)
    return completed.returncode == 0


def log_findings(repo: Path, ledger: Path, log_glob: str) -> list[Finding]:
    """The host's failures to declare the event log ``-text``."""
    rule = f"{log_glob} -text"
    findings = []
    for name in samples(log_glob):
        relative = (ledger / name).as_posix()
        value = text_attribute(repo, relative)
        if value != "unset":
            findings.append(
                Finding(
                    path=relative,
                    detail=(
                        f"git reports text: {value}, so a checkout may rewrite the log's "
                        f"bytes and an event id is content-derived"
                    ),
                    remedy=f"add to .gitattributes, after any `*` rule:  {rule}",
                )
            )
    return findings


def derived_findings(repo: Path, ledger: Path, patterns: Sequence[str]) -> list[Finding]:
    """The host's failures to keep the derived files out of the repository."""
    findings = []
    for pattern in patterns:
        rule = (ledger / pattern).as_posix()
        for name in samples(pattern):
            relative = (ledger / name).as_posix()
            if not is_ignored_by_a_rule(repo, relative):
                findings.append(
                    Finding(
                        path=relative,
                        detail=(
                            "no ignore rule matches it, so a derived file is offered as "
                            "untracked and can be committed beside the log it is folded from"
                        ),
                        remedy=f"add to .gitignore:  {rule}",
                    )
                )
            elif is_tracked(repo, relative):
                findings.append(
                    Finding(
                        path=relative,
                        detail=(
                            f"the ignore rule `{rule}` matches it but it is already in the "
                            f"index, and an ignore rule does not un-commit a file"
                        ),
                        remedy=f"run:  git rm --cached {relative}",
                    )
                )
    return findings


def collect(repo: Path, ledger: Path = LEDGER_DIR, kit_dir: Path = KIT_DIR) -> list[Finding]:
    """Every deployment requirement ``repo`` does not satisfy.

    Args:
        repo: The host repository's root.
        ledger: The ledger directory, relative to ``repo``.
        kit_dir: The tracker kit's directory, relative to ``repo``.

    Returns:
        The findings, ordered by path then detail.

    Raises:
        DeploymentError: The kit could not be read, or git refused a question.
    """
    kit = load_kit(repo / kit_dir)
    findings = [
        *log_findings(repo, ledger, kit.events.LOG_GLOB),
        *derived_findings(repo, ledger, kit.DERIVED_PATTERNS),
    ]
    return sorted(findings, key=lambda finding: finding.key)


def report(findings: Iterable[Finding]) -> None:
    """Print each finding as the rule the host lacks, then how to add it."""
    for finding in findings:
        print(f"kit-deployment: {finding.path}: {finding.detail}", file=sys.stderr)
        print(f"kit-deployment:   {finding.remedy}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: report the deployment requirements this host does not satisfy."""
    parser = argparse.ArgumentParser(
        description="Check a host repository against the tracker kit's deployment requirements."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=REPO_ROOT,
        help="the host repository's root (default: this script's repository)",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=LEDGER_DIR,
        help=f"the ledger directory, relative to --repo (default: {LEDGER_DIR.as_posix()})",
    )
    args = parser.parse_args(argv)

    try:
        findings = collect(args.repo, args.ledger)
    except DeploymentError as exc:
        print(f"kit-deployment: {exc}", file=sys.stderr)
        return 1

    if findings:
        report(findings)
        return 1
    print(f"kit-deployment: {args.ledger.as_posix()} satisfies both kit requirements")
    return 0


if __name__ == "__main__":
    sys.exit(main())
