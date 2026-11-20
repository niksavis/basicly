from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from . import checkout, gh_cli, landing_guard, tracker, verify
from .config import load_verify_config

MODE = landing_guard.CI_MODE
WORKFLOW = "quality-gates.yml"
REF_PREFIX = "land"
LEDGER = ".basicly/ledger"
PROOFS = Path(".basicly/usage/ci-proofs.json")
POLL_S = 30.0
FIND_S = 120.0
TIMEOUT_S = 900.0
REUSE_DEPTH = 30
FALLBACK = "or land locally with --mode full"

Gh = Callable[[list[str]], str | None]


class CiLandingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    lane: str
    record: str
    base: str
    head: str


@dataclass(frozen=True)
class Clock:
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic


def _git(root: Path, *args: str, check: bool = True) -> str:
    return checkout.git(list(args), cwd=root, check=check).stdout.strip()


def same_outside_ledger(root: Path, first: str, second: str) -> bool:
    done = checkout.git(
        ["diff", "--quiet", first, second, "--", ".", f":(exclude){LEDGER}"], cwd=root, check=False
    )
    return done.returncode == 0


def local_checks(worktree: Path) -> verify.VerifyReport:
    config = load_verify_config(worktree)
    kept = tuple(check for check in config.checks if check.local_only)
    return verify.run_verify(worktree, "full", replace(config, checks=kept), capture_receipts=True)


def _proofs(root: Path) -> dict[str, dict[str, str]]:
    try:
        found = json.loads((root / PROOFS).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _remember(root: Path, lane: str, proof: dict[str, str]) -> None:
    proofs = _proofs(root)
    proofs[lane] = proof
    path = root / PROOFS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proofs, indent=2, sort_keys=True), encoding="utf-8")


def proved(root: Path, lane: str) -> dict[str, str]:
    return _proofs(root).get(lane, {})


def _ref(lane: str, attempt: int) -> str:
    return f"{REF_PREFIX}/{lane}/{attempt}"


def _push_ref(root: Path, head: str, ref: str) -> None:
    done = checkout.git(["push", "origin", f"{head}:refs/heads/{ref}"], cwd=root, check=False)
    if done.returncode != 0:
        raise CiLandingError(
            f"git push of the candidate to {ref} was refused; nothing is landed and nothing was "
            f"force-pushed; inspect git ls-remote origin {ref}, {FALLBACK}"
        )


def _find(candidate: Candidate, ref: str, gh: Gh, clock: Clock) -> dict:
    started = clock.monotonic()
    fields = "databaseId,url,status,conclusion,attempt"
    while True:
        listed = gh([
            "run",
            "list",
            "--workflow",
            WORKFLOW,
            "--commit",
            candidate.head,
            "--event",
            "workflow_dispatch",
            "--json",
            fields,
        ])
        runs = json.loads(listed) if listed else []
        if runs:
            return runs[0]
        if clock.monotonic() - started >= FIND_S:
            raise CiLandingError(
                f"dispatched {WORKFLOW} on {ref} but no run for {candidate.head[:12]} appeared "
                f"in {FIND_S / 60:.0f} min; nothing is landed; check gh run list --branch {ref}"
            )
        clock.sleep(POLL_S)


def _wait(run: dict, gh: Gh, clock: Clock) -> dict:
    started = clock.monotonic()
    while True:
        viewed = gh([
            "run",
            "view",
            str(run["databaseId"]),
            "--json",
            "status,conclusion,attempt,url,jobs",
        ])
        state = json.loads(viewed) if viewed else {}
        if state.get("status") == "completed":
            return state
        if clock.monotonic() - started >= TIMEOUT_S:
            unread = "" if viewed else "; gh could not read it, check gh auth status"
            raise CiLandingError(
                f"{WORKFLOW} run {run.get('url')} did not finish in {TIMEOUT_S / 60:.0f} min"
                f"{unread}; nothing is landed; run the merge again for a new run, {FALLBACK}"
            )
        clock.sleep(POLL_S)


def verdict(state: dict, candidate: Candidate) -> str:
    url = state.get("url")
    conclusion = state.get("conclusion")
    attempt = state.get("attempt")
    if conclusion == "success" and attempt == 1:
        return ""
    if conclusion == "success":
        return (
            f"{WORKFLOW} run {url} succeeded only on attempt {attempt}; a landing accepts a "
            "first-attempt pass; run the merge again for a new run"
        )
    failed = [
        str(job.get("name")) for job in state.get("jobs", []) if job.get("conclusion") == "failure"
    ]
    return (
        f"{WORKFLOW} run {url} ended {conclusion} in {', '.join(failed) or 'no named job'}; "
        f"{candidate.lane} is not landed; fix the lane and run basicly worktree merge "
        f"{candidate.lane} --bead {candidate.record} --mode ci again"
    )


def _spent(root: Path, candidate: Candidate, gh: Gh, tried: list[str]) -> str:
    heads = [candidate.head]
    heads += [
        h for h in tried if h != candidate.head and same_outside_ledger(root, h, candidate.head)
    ]
    for head in heads:
        listed = gh([
            "run",
            "list",
            "--workflow",
            WORKFLOW,
            "--commit",
            head,
            "--json",
            "databaseId,url,status,conclusion",
        ])
        if listed is None:
            raise CiLandingError(
                f"cannot read earlier {WORKFLOW} runs of {head[:12]} with gh; "
                f"run gh auth login, {FALLBACK}"
            )
        for run in json.loads(listed):
            if run.get("status") == "completed" and run.get("conclusion") != "success":
                return (
                    f"{WORKFLOW} run {run.get('url')} already ended {run.get('conclusion')} "
                    f"on this tree; a landing takes only a first attempt; change the lane, "
                    f"then run basicly worktree merge {candidate.lane} --bead {candidate.record} "
                    "--mode ci again"
                )
    return ""


def prove(
    root: Path, candidate: Candidate, gh: Gh = gh_cli.run, clock: Clock | None = None
) -> dict[str, str]:
    clock = clock or Clock()
    earlier = proved(root, candidate.lane)
    if earlier.get("head") and same_outside_ledger(root, earlier["head"], candidate.head):
        return earlier
    tried = earlier.get("dispatched", "").split()
    if refused := _spent(root, candidate, gh, tried):
        raise CiLandingError(refused)
    attempt = int(earlier.get("attempt", "0")) + 1
    ref = _ref(candidate.lane, attempt)
    dispatched = " ".join([*tried, candidate.head])
    _remember(root, candidate.lane, {"attempt": str(attempt), "dispatched": dispatched})
    _push_ref(root, candidate.head, ref)
    if gh(["workflow", "run", WORKFLOW, "--ref", ref, "-f", f"base={candidate.base}"]) is None:
        raise CiLandingError(f"cannot dispatch {WORKFLOW} with gh; run gh auth login, {FALLBACK}")
    state = _wait(_find(candidate, ref, gh, clock), gh, clock)
    if refused := verdict(state, candidate):
        raise CiLandingError(refused)
    proof = {
        "head": candidate.head,
        "tree": _git(root, "rev-parse", f"{candidate.head}^{{tree}}"),
        "run": str(state.get("url")),
        "ref": ref,
        "attempt": str(attempt),
        "dispatched": dispatched,
    }
    _remember(root, candidate.lane, proof)
    return proof


def _record(root: Path, record: str, proof: dict[str, str]) -> str:
    if not proof.get("head") or not same_outside_ledger(root, proof["head"], "HEAD"):
        return (
            "the landed tree differs from the proved tree outside the ledger; main stays "
            f"unverified and unpushed; run basicly verify --mode full --issue {record}"
        )
    tracker.add_artifact(
        root,
        record,
        "landing-verification",
        {
            "status": "ci-verified",
            "head": _git(root, "rev-parse", "HEAD"),
            "base": checkout.current_branch(root),
            "run": proof["run"],
            "tree": proof["tree"],
        },
    )
    return ""


def finish(root: Path, lane: str, record: str) -> list[str]:
    proof = proved(root, lane)
    if refused := _record(root, record, proof):
        return [refused]
    notes: list[str] = []
    base = checkout.current_branch(root)
    pushed = checkout.git(["push", "origin", base], cwd=root, check=False)
    if pushed.returncode != 0:
        notes.append(
            f"git push origin {base} was refused; {base} is landed locally only and nothing was "
            f"force-pushed; run git pull --ff-only and push {base}"
        )
    notes += drop_refs(root, lane)
    proofs = _proofs(root)
    proofs.pop(lane, None)
    (root / PROOFS).write_text(json.dumps(proofs, indent=2, sort_keys=True), encoding="utf-8")
    return notes


def drop_refs(root: Path, lane: str) -> list[str]:
    notes: list[str] = []
    for attempt in range(1, int(proved(root, lane).get("attempt", "0")) + 1):
        ref = _ref(lane, attempt)
        listed = checkout.git(
            ["ls-remote", "--heads", "origin", f"refs/heads/{ref}"], cwd=root, check=False
        )
        if listed.returncode == 0 and not listed.stdout.strip():
            continue
        gone = checkout.git(["push", "origin", "--delete", ref], cwd=root, check=False)
        if gone.returncode != 0:
            notes.append(f"could not delete {ref} on origin; it stays listed for the owner")
    return notes


def _ledger_only(root: Path, sha: str) -> bool:
    paths = _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
    return bool(paths) and all(path.startswith(f"{LEDGER}/") for path in paths)


def _proved_commit(root: Path, head: str) -> str:
    sha = head
    for _ in range(REUSE_DEPTH):
        parents = _git(root, "rev-list", "--parents", "-n", "1", sha).split()[1:]
        if len(parents) == 1 and _ledger_only(root, sha):
            sha = parents[0]
            continue
        if len(parents) == 2 and same_outside_ledger(root, sha, parents[1]):
            return parents[1]
        return sha
    return sha


def reusable(root: Path, head: str, current_run: str, gh: Gh = gh_cli.run) -> str:
    candidate = _proved_commit(root, head)
    fields = "databaseId,url,conclusion,attempt"
    listed = gh(["run", "list", "--workflow", WORKFLOW, "--commit", candidate, "--json", fields])
    for run in json.loads(listed) if listed else []:
        if (
            run.get("conclusion") == "success"
            and run.get("attempt") == 1
            and str(run.get("databaseId")) != current_run
        ):
            return str(run.get("url"))
    return ""
