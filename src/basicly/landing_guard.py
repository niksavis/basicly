from __future__ import annotations

import json
import shlex
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol

from . import checkout, pytest_inputs, tracker, tracker_paths
from .config import VerifyConfig, load_verify_config
from .verify_artifact import CheckResult, VerifyReport, write_run_artifact
from .verify_receipt import changed_components, components


class LandingSession(Protocol):
    @property
    def base(self) -> str: ...

    @property
    def branch(self) -> str: ...

    @property
    def path(self) -> Path: ...


@dataclass(frozen=True)
class LandingSubject:
    base: str
    branch: str
    destination: str
    candidate: str
    tree: str
    workspace: str
    mode: str
    inputs: str
    components: tuple[tuple[str, str], ...] = ()
    record: str = ""


def _head(root: Path, ref: str) -> str:
    value = checkout.git(["rev-parse", "--verify", ref], cwd=root).stdout.strip()
    if len(value) not in {40, 64} or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("Cannot capture immutable landing identity")
    return value


def _inputs(workspace: Path, mode: str, candidate: str, config: VerifyConfig | None = None) -> str:
    config = config or load_verify_config(workspace)
    commands = [check.command[0] for check in config.for_mode(mode)]
    configuration = json.loads(json.dumps(asdict(config), default=sorted))
    return pytest_inputs.landing_fingerprint(workspace, commands, candidate, configuration)


class SubjectUnavailableError(ValueError):
    pass


def capture(root: Path, session: LandingSession, mode: str, *, record: str = "") -> LandingSubject:
    try:
        return _capture(root, session, mode, record)
    except (OSError, ValueError, RuntimeError) as error:
        raise SubjectUnavailableError(
            "Cannot capture landing identity; inspect candidate and retry"
        ) from error


def _capture(root: Path, session: LandingSession, mode: str, record: str) -> LandingSubject:
    destination = _head(root, f"refs/heads/{session.base}")
    candidate = _head(root, f"refs/heads/{session.branch}")
    if _head(session.path, "HEAD") != candidate:
        raise ValueError("Candidate checkout differs from its branch; retry landing")
    inputs = _inputs(session.path, mode, candidate)
    return LandingSubject(
        session.base,
        session.branch,
        destination,
        candidate,
        _head(root, f"{candidate}^{{tree}}"),
        str(session.path.resolve()),
        mode,
        str(inputs),
        components(inputs),
        record,
    )


def _changed(root: Path, subject: LandingSubject) -> str:
    if _head(root, f"refs/heads/{subject.base}") != subject.destination:
        return "destination reference changed; retry landing"
    if checkout.current_branch(root) != subject.base:
        return "destination checkout changed; retry landing"
    if _head(root, f"refs/heads/{subject.branch}") != subject.candidate:
        return "candidate reference changed; requeue landing"
    workspace = Path(subject.workspace)
    if _head(workspace, "HEAD") != subject.candidate:
        return "candidate checkout changed; retry landing"
    current = _inputs(workspace, subject.mode, subject.candidate)
    if current != subject.inputs:
        return changed_components(current, subject.components) or "landing inputs changed"
    return ""


def changed(root: Path, subject: LandingSubject) -> str:
    try:
        return _changed(root, subject)
    except OSError, ValueError, RuntimeError:
        return "landing references or inputs unavailable; inspect candidate and retry landing"


def destination_inputs(root: Path, mode: str, config: VerifyConfig | None = None) -> str:
    return _inputs(root, mode, _head(root, "HEAD"), config)


def blocker(root: Path, issue: str, *, allow_owner: bool = True) -> str:
    for identity, fact in tracker.artifact_states(root, "landing-verification").items():
        if (
            isinstance(fact, dict)
            and fact.get("status") == "unverified"
            and (identity != issue or not allow_owner)
        ):
            return (
                f"destination is unverified after {identity}; repair it and run "
                f"basicly verify --mode full --issue {identity} before another landing or close"
            )
    return ""


def record_verification(
    root: Path, issue: str, *, passed: bool, proof: str = "", head: str = ""
) -> None:
    if not issue:
        return
    states = tracker.artifact_states(root, "landing-verification")
    if issue not in states:
        return
    previous = states[issue]
    if passed and (not isinstance(previous, dict) or previous.get("status") != "unverified"):
        return
    if passed and (not proof or proof != destination_inputs(root, "full")):
        return
    tracker.add_artifact(
        root,
        issue,
        "landing-verification",
        {
            "status": "verified" if passed else "unverified",
            "head": head or _head(root, "HEAD"),
            "base": checkout.current_branch(root),
        },
    )


def before_verification(root: Path, mode: str, config: VerifyConfig) -> str:
    if mode != "full" or not blocker(root, "", allow_owner=False):
        return ""
    try:
        return destination_inputs(root, mode, config)
    except OSError, ValueError, RuntimeError:
        return "unavailable"


def finish_verification(
    root: Path, report: VerifyReport, before: str, config: VerifyConfig
) -> VerifyReport:
    if before:
        try:
            after = destination_inputs(root, report.mode, config)
        except OSError, ValueError, RuntimeError:
            after = ""
        if before != after:
            report = replace(
                report,
                receipts=(),
                results=(
                    *report.results,
                    CheckResult(
                        "landing-inputs",
                        "fail",
                        1,
                        "Destination inputs changed or became unavailable; "
                        "keep destination unverified",
                    ),
                ),
            )
        else:
            report = replace(report, subject_fingerprint=str(after))
    write_run_artifact(root, report)
    return report


class GuardUnavailableError(ValueError):
    pass


def require_stable_engine(root: Path, subject: LandingSubject) -> None:
    engine = Path(__file__).resolve().parent
    if (
        engine.is_relative_to(root.resolve())
        and checkout.git(
            [
                "diff",
                "--quiet",
                subject.destination,
                subject.candidate,
                "--",
                engine.relative_to(root.resolve()).as_posix(),
            ],
            cwd=root,
            check=False,
        ).returncode
    ):
        raise GuardUnavailableError(
            "Landing changes the executing engine; rerun from the candidate worktree "
            "engine with the destination as the working directory"
        )


def guarded_merge(
    root: Path, subject: LandingSubject, command: list[str]
) -> subprocess.CompletedProcess[str]:
    require_stable_engine(root, subject)
    enabled = checkout.git(
        ["config", "--type=bool", "--get", "hook.reference-transaction.enabled"],
        cwd=root,
        check=False,
    )
    if enabled.returncode not in {0, 1} or (
        enabled.returncode == 0 and enabled.stdout.strip() != "true"
    ):
        raise GuardUnavailableError(
            "Reference transaction hooks are disabled or invalid; inspect Git configuration"
        )
    directory = checkout.git_common_dir(root) / "basicly-landing"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise GuardUnavailableError("Landing receipt directory is a symlink; inspect Git state")
    path = directory / f"{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(asdict(subject)), encoding="utf-8")
    name = f"basicly-landing-{path.stem}"
    probe = f"basicly-landing-probe-{path.stem}"
    invoke = shlex.join([
        Path(sys.executable).as_posix(),
        "-m",
        "basicly.landing_guard",
        path.as_posix(),
    ])
    options = [
        "-c",
        "hook.reference-transaction.enabled=true",
        "-c",
        f"hook.{name}.command={invoke}",
        "-c",
        f"hook.{name}.event=reference-transaction",
        "-c",
        f"hook.{name}.event={probe}",
        "-c",
        f"hook.{name}.enabled=true",
    ]
    ready = checkout.git(
        [*options, "hook", "run", "--allow-unknown-hook-name", probe, "--", "probe"],
        cwd=root,
        check=False,
    )
    if ready.returncode or not path.with_suffix(".probe").is_file():
        raise GuardUnavailableError(
            "Git cannot run the configured landing guard; use Git with named-hook support"
        )
    result = checkout.git([*options, *command], cwd=root, check=False)
    if result.returncode == 0 and not path.with_suffix(".receipt").is_file():
        raise ValueError("Publication has no prepared transaction receipt; inspect the destination")
    if result.returncode == 0:
        proposed = path.with_suffix(".receipt").read_text(encoding="utf-8")
        if _head(root, "HEAD") != proposed:
            raise ValueError("Destination changed after publication; keep it unverified")
        metadata = checkout.git(
            ["show", "-s", "--format=%P%n%T", proposed], cwd=root
        ).stdout.splitlines()
        if metadata != [f"{subject.destination} {subject.candidate}", subject.tree]:
            raise ValueError("Published tree or parents differ from the verified candidate")
        if checkout.git(["write-tree"], cwd=root).stdout.strip() != subject.tree:
            raise ValueError("Destination index differs from published tree")
        changed_paths = checkout.git(["diff", "--name-only", "-z"], cwd=root).stdout.split("\0")
        if any(path and not tracker_paths.is_ledger_path(path) for path in changed_paths):
            raise ValueError("Destination checkout changed during publication; preserve user work")
    return result


def _read_subject(path: Path) -> LandingSubject:
    with path.open("rb") as stream:
        encoded = stream.read(65537)
    if len(encoded) > 65536:
        raise ValueError("Landing subject exceeds its size limit")
    raw = json.loads(encoded)
    required = {"base", "branch", "destination", "candidate", "tree", "workspace", "mode", "inputs"}
    if (
        not isinstance(raw, dict)
        or not required.issubset(raw)
        or any(not isinstance(raw[key], str) or not raw[key] for key in required)
    ):
        raise ValueError("Malformed landing subject")
    for key in ("destination", "candidate", "tree", "inputs"):
        if len(raw[key]) not in ({64} if key == "inputs" else {40, 64}) or any(
            char not in "0123456789abcdef" for char in raw[key]
        ):
            raise ValueError("Malformed landing subject identity")
    raw["components"] = tuple(tuple(row) for row in raw.get("components", ()))
    subject = LandingSubject(**raw)
    if subject.mode not in {"full", "fast", "staged"}:
        raise ValueError("Unknown landing verification mode")
    if not Path(subject.workspace).is_absolute() or not isinstance(subject.record, str):
        raise ValueError("Malformed landing subject workspace or record")
    return subject


def _prepare(path: Path) -> None:
    subject = _read_subject(path)
    if checkout.git_common_dir(Path(subject.workspace)) != checkout.git_common_dir(Path.cwd()):
        raise ValueError("Candidate workspace belongs to another repository")
    transaction = sys.stdin.read(65537)
    if len(transaction) > 65536:
        raise ValueError("Reference transaction exceeds its size limit")
    rows = [line.split() for line in transaction.splitlines()]
    if any(len(row) != 3 for row in rows):
        raise ValueError("Malformed reference transaction")
    protected = [
        row
        for row in rows
        if len(row) == 3 and (row[2].startswith("refs/heads/") or row[2] == "HEAD")
    ]
    if not protected:
        return
    if any(
        row[0] != subject.destination or row[2] not in {"HEAD", f"refs/heads/{subject.base}"}
        for row in protected
    ):
        raise ValueError("destination transaction changed")
    if reason := changed(Path.cwd(), subject):
        raise ValueError(reason)
    proposed = {row[1] for row in protected}
    if len(proposed) != 1:
        raise ValueError("destination transaction has conflicting identities")
    oid = proposed.pop()
    metadata = checkout.git(["show", "-s", "--format=%P%n%T", oid]).stdout.splitlines()
    if metadata != [f"{subject.destination} {subject.candidate}", subject.tree]:
        raise ValueError("proposed merge differs from the captured candidate")
    record_verification(Path.cwd(), subject.record, passed=False, head=oid)
    path.with_suffix(".receipt").write_text(oid, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        return 1
    path, phase = Path(args[0]), args[1]
    expected = checkout.git_common_dir(Path.cwd()) / "basicly-landing"
    if (
        path.parent != expected
        or expected.is_symlink()
        or path.is_symlink()
        or not path.is_file()
        or path.suffix != ".json"
        or len(path.stem) != 32
        or any(char not in "0123456789abcdef" for char in path.stem)
    ):
        return 1
    if phase == "probe":
        path.with_suffix(".probe").write_text("ready", encoding="utf-8")
        return 0
    if phase != "prepared":
        return 0
    try:
        _prepare(path)
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        print(
            f"Landing publication refused: {type(error).__name__}; preserve checkout and retry",
            file=sys.stderr,
        )
        path.with_suffix(".failure").write_text(str(error), encoding="utf-8")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
