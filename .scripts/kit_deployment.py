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

KIT_DIR = Path(".basicly") / "core" / "kit" / "tracker"
LEDGER_DIR = Path(".basicly") / "ledger"

GLOB_FILLS = ("0001", "2026q1")

_SNAPSHOT_MODULE_NAME = "kit_deployment_snapshot"


class DeploymentError(Exception):
    pass


@dataclass(frozen=True)
class Finding:
    path: str
    detail: str
    remedy: str

    @property
    def key(self) -> str:
        return f"{self.path}:{self.detail}"


def load_kit(kit_dir: Path) -> Any:

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

    if "*" not in pattern:
        return (pattern,)
    return tuple(pattern.replace("*", fill) for fill in GLOB_FILLS)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 B607
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _fatal(completed: subprocess.CompletedProcess[str], question: str) -> None:

    if completed.returncode >= 128:
        detail = completed.stderr.strip() or f"git exited {completed.returncode}"
        raise DeploymentError(f"git could not answer {question}: {detail}")


def attribute(repo: Path, path: str, name: str) -> str:

    completed = _git(repo, "check-attr", "-z", name, "--", path)
    _fatal(completed, f"the {name} attribute of {path}")
    fields = completed.stdout.split("\0")
    if len(fields) < 3:
        raise DeploymentError(f"git check-attr gave no answer for {path}")
    return fields[2]


def is_ignored_by_a_rule(repo: Path, path: str) -> bool:

    completed = _git(repo, "check-ignore", "-q", "--no-index", "--", path)
    _fatal(completed, f"the ignore rules for {path}")
    return completed.returncode == 0


def is_tracked(repo: Path, path: str) -> bool:
    completed = _git(repo, "ls-files", "--error-unmatch", "--", path)
    return completed.returncode == 0


def log_findings(repo: Path, ledger: Path, log_glob: str) -> list[Finding]:
    rule = f"{log_glob} -text merge=union"
    remedy = f"add to .gitattributes, after any `*` rule:  {rule}"
    findings = []
    for name in samples(log_glob):
        relative = (ledger / name).as_posix()
        text = attribute(repo, relative, "text")
        if text != "unset":
            findings.append(
                Finding(
                    path=relative,
                    detail=(
                        f"git reports text: {text}, so a checkout may rewrite the log's "
                        f"bytes and an event id is content-derived"
                    ),
                    remedy=remedy,
                )
            )
        merge = attribute(repo, relative, "merge")
        if merge != "union":
            findings.append(
                Finding(
                    path=relative,
                    detail=(
                        f"git reports merge: {merge}, so two branches that each append an "
                        f"event conflict instead of keeping both"
                    ),
                    remedy=remedy,
                )
            )
    return findings


def derived_findings(repo: Path, ledger: Path, patterns: Sequence[str]) -> list[Finding]:
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
            if attribute(repo, relative, "merge") == "union":
                findings.append(
                    Finding(
                        path=relative,
                        detail=(
                            "git reports merge: union on a derived file, which is rewritten "
                            "rather than appended, so a merge would concatenate two of them"
                        ),
                        remedy=f"narrow the merge=union rule in .gitattributes off  {rule}",
                    )
                )
    return findings


def collect(repo: Path, ledger: Path = LEDGER_DIR, kit_dir: Path = KIT_DIR) -> list[Finding]:

    kit = load_kit(repo / kit_dir)
    findings = [
        *log_findings(repo, ledger, kit.events.LOG_GLOB),
        *derived_findings(repo, ledger, kit.DERIVED_PATTERNS),
    ]
    return sorted(findings, key=lambda finding: finding.key)


def report(findings: Iterable[Finding]) -> None:
    for finding in findings:
        print(f"kit-deployment: {finding.path}: {finding.detail}", file=sys.stderr)
        print(f"kit-deployment:   {finding.remedy}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
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
    print(f"kit-deployment: {args.ledger.as_posix()} satisfies the kit's deployment requirements")
    return 0


if __name__ == "__main__":
    sys.exit(main())
