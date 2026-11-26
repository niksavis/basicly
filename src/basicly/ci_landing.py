from __future__ import annotations

import json
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from . import checkout, gh_cli, landing_guard, tracker, verify
from .config import CONFIG_FILE, load_landing_workflows, load_verify_config

MODE = landing_guard.CI_MODE
WORKFLOWS = ("quality-gates.yml", "basicly-gates.yml")
WORKFLOW_DIR = Path(".github/workflows")
REF_PREFIX = "land"
LEDGER = ".basicly/ledger"
PROOFS = Path(".basicly/usage/ci-proofs.json")
POLL_S = 30.0
FIND_S = 120.0
TIMEOUT_S = 900.0
RERUNS = 1
REUSE_DEPTH = 30
FALLBACK = "or land locally with --mode full"

Gh = Callable[[list[str]], str | None]


class CiLandingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Workflow:
    name: str
    inputs: frozenset[str]


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


def _dispatch_inputs(path: Path) -> frozenset[str] | None:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError, yaml.YAMLError:
        return None
    triggers = loaded.get("on", loaded.get(True)) if isinstance(loaded, dict) else None
    if isinstance(triggers, str):
        triggers = {triggers: None}
    if isinstance(triggers, list):
        triggers = dict.fromkeys(triggers)
    if not isinstance(triggers, dict) or "workflow_dispatch" not in triggers:
        return None
    dispatch = triggers["workflow_dispatch"]
    inputs = dispatch.get("inputs") if isinstance(dispatch, dict) else None
    return frozenset(inputs) if isinstance(inputs, dict) else frozenset()


def _named(root: Path, names: tuple[str, ...]) -> tuple[Workflow, ...]:
    found = []
    for name in names:
        inputs = _dispatch_inputs(root / WORKFLOW_DIR / name)
        if inputs is None:
            raise CiLandingError(
                f"[landing] workflows in {CONFIG_FILE} names {name}, but "
                f"{(WORKFLOW_DIR / name).as_posix()} is missing or has no workflow_dispatch "
                f"trigger; add the trigger or remove the name, {FALLBACK}"
            )
        found.append(Workflow(name, inputs))
    return tuple(found)


def workflows(root: Path) -> tuple[Workflow, ...]:
    try:
        configured = load_landing_workflows(root)
    except ValueError as error:
        raise CiLandingError(str(error)) from error
    if configured:
        return _named(root, configured)
    found = [
        Workflow(name, inputs)
        for name in WORKFLOWS
        if (inputs := _dispatch_inputs(root / WORKFLOW_DIR / name)) is not None
    ]
    if len(found) == 1:
        return (found[0],)
    if found:
        listed = ", ".join(f'"{flow.name}"' for flow in found)
        raise CiLandingError(
            f"{' and '.join(flow.name for flow in found)} can both prove a landing; name the "
            f"ones main needs green in {CONFIG_FILE}: [landing] workflows = [{listed}]"
        )
    raise CiLandingError(
        f"no workflow in {WORKFLOW_DIR.as_posix()} can prove a landing: looked for "
        f"{' and '.join(WORKFLOWS)} with a workflow_dispatch trigger; add the trigger to one, "
        f"or name the workflows in {CONFIG_FILE} as [landing] workflows = [...], {FALLBACK}"
    )


def gh_cause(gh: Gh, argv: list[str]) -> str:
    if gh(["auth", "status"]) is None:
        return "gh is not installed or not logged in; run gh auth login"
    return f"gh is logged in, so gh refused this call; run gh {shlex.join(argv)} to read why"


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


def _find(candidate: Candidate, flow: str, ref: str, gh: Gh, clock: Clock) -> dict:
    started = clock.monotonic()
    fields = "databaseId,url,status,conclusion,attempt"
    argv = ["run", "list", "--workflow", flow, "--commit", candidate.head]
    argv += ["--event", "workflow_dispatch", "--json", fields]
    while True:
        listed = gh(argv)
        runs = json.loads(listed) if listed else []
        if runs:
            return runs[0]
        if clock.monotonic() - started >= FIND_S:
            raise CiLandingError(
                f"dispatched {flow} on {ref} but no run for {candidate.head[:12]} appeared "
                f"in {FIND_S / 60:.0f} min; nothing is landed; check gh run list --branch {ref}"
            )
        clock.sleep(POLL_S)


def _wait(flow: str, run: dict, gh: Gh, clock: Clock) -> dict:
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
                f"{flow} run {run.get('url')} did not finish in {TIMEOUT_S / 60:.0f} min"
                f"{unread}; nothing is landed; run the merge again for a new run, {FALLBACK}"
            )
        clock.sleep(POLL_S)


def verdict(
    state: dict, candidate: Candidate, flow: str = WORKFLOWS[0], retry: str = f"--mode {MODE}"
) -> str:
    url = state.get("url")
    conclusion = state.get("conclusion")
    attempt = state.get("attempt")
    if conclusion == "success" and attempt == 1:
        return ""
    if conclusion == "success":
        return (
            f"{flow} run {url} succeeded only on attempt {attempt}; a landing accepts a "
            "first-attempt pass; run the merge again for a new run"
        )
    failed = [
        str(job.get("name")) for job in state.get("jobs", []) if job.get("conclusion") == "failure"
    ]
    return (
        f"{flow} run {url} ended {conclusion} in {', '.join(failed) or 'no named job'}; "
        f"{candidate.lane} is not landed; fix the lane and run basicly worktree merge "
        f"{candidate.lane} --bead {candidate.record} {retry} again"
    )


def failed_runs(
    root: Path, candidate: Candidate, flow: str, gh: Gh, tried: list[str]
) -> list[dict]:
    heads = [candidate.head]
    heads += [
        h for h in tried if h != candidate.head and same_outside_ledger(root, h, candidate.head)
    ]
    failed = []
    for head in heads:
        argv = ["run", "list", "--workflow", flow, "--commit", head]
        listed = gh([*argv, "--json", "databaseId,url,status,conclusion"])
        if listed is None:
            raise CiLandingError(
                f"cannot read earlier {flow} runs of {head[:12]}; {gh_cause(gh, argv)}, {FALLBACK}"
            )
        failed += [
            run
            for run in json.loads(listed)
            if run.get("status") == "completed" and run.get("conclusion") != "success"
        ]
    return failed


def spent(
    failed: list[dict],
    candidate: Candidate,
    flow: str = WORKFLOWS[0],
    retry: str = f"--mode {MODE}",
) -> str:
    if len(failed) <= RERUNS:
        return ""
    urls = ", ".join(str(run.get("url")) for run in failed)
    return (
        f"{flow} already failed {len(failed)} times on this tree ({urls}); a landing takes "
        f"a first attempt and {RERUNS} rerun; change the lane, then run basicly worktree merge "
        f"{candidate.lane} --bead {candidate.record} {retry} again"
    )


def _dispatch(candidate: Candidate, flow: Workflow, ref: str, gh: Gh) -> None:
    argv = ["workflow", "run", flow.name, "--ref", ref]
    if "base" in flow.inputs:
        argv += ["-f", f"base={candidate.base}"]
    if gh(argv) is None:
        raise CiLandingError(
            f"cannot dispatch {flow.name} on {ref}; {gh_cause(gh, argv)}, {FALLBACK}"
        )


def prove(
    root: Path, candidate: Candidate, gh: Gh = gh_cli.run, clock: Clock | None = None
) -> dict[str, str]:
    clock = clock or Clock()
    earlier = proved(root, candidate.lane)
    if earlier.get("head") and same_outside_ledger(root, earlier["head"], candidate.head):
        return earlier
    flows = workflows(root)
    tried = earlier.get("dispatched", "").split()
    failed_before = 0
    for flow in flows:
        failed = failed_runs(root, candidate, flow.name, gh, tried)
        if refused := spent(failed, candidate, flow.name):
            raise CiLandingError(refused)
        failed_before += len(failed)
    attempt = int(earlier.get("attempt", "0")) + 1
    ref = _ref(candidate.lane, attempt)
    dispatched = " ".join([*tried, candidate.head])
    _remember(root, candidate.lane, {"attempt": str(attempt), "dispatched": dispatched})
    _push_ref(root, candidate.head, ref)
    for flow in flows:
        _dispatch(candidate, flow, ref, gh)
    urls = []
    for flow in flows:
        state = _wait(flow.name, _find(candidate, flow.name, ref, gh, clock), gh, clock)
        if refused := verdict(state, candidate, flow.name):
            raise CiLandingError(refused)
        urls.append(str(state.get("url")))
    proof = {
        "head": candidate.head,
        "tree": _git(root, "rev-parse", f"{candidate.head}^{{tree}}"),
        "run": " ".join(urls),
        "ref": ref,
        "attempt": str(attempt),
        "dispatched": dispatched,
        "failed_before": str(failed_before),
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
    flow = workflows(root)[0].name
    listed = gh(["run", "list", "--workflow", flow, "--commit", candidate, "--json", fields])
    for run in json.loads(listed) if listed else []:
        if (
            run.get("conclusion") == "success"
            and run.get("attempt") == 1
            and str(run.get("databaseId")) != current_run
        ):
            return str(run.get("url"))
    return ""
