from __future__ import annotations

import glob
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import checkout, ci_landing, gh_cli, remote_branches, tracker
from .config import CONFIG_FILE, load_landing_workflows
from .worktree import unlanded_paths

ADAPTER = "pr"
EVENT = "pull_request"
BRANCH_PREFIX = remote_branches.PR_PREFIX
REMOTE = "origin"
RETRY = f"--delivery {ADAPTER}"
UNREAD_POLLS = 3


class PrDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Landed:
    commit: str
    pull_request: str
    runs: tuple[str, ...]
    notes: tuple[str, ...]


def branch_name(lane: str, head: str) -> str:
    return f"{BRANCH_PREFIX}/{lane}/{head[:12]}"


def merge_message(record: str, number: str, branch: str, base: str) -> tuple[str, str]:
    subject = f"chore(worktree): merge a pull request back to its base ({record})"
    body = f"Integrate pull request {number} ({branch}) into {base}.\n\n{record}"
    return subject, body


def _pull_request_trigger(path: Path) -> dict | None:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError, yaml.YAMLError:
        return None
    triggers = loaded.get("on", loaded.get(True)) if isinstance(loaded, dict) else None
    if triggers == EVENT or (isinstance(triggers, list) and EVENT in triggers):
        return {}
    if not isinstance(triggers, dict) or EVENT not in triggers:
        return None
    found = triggers[EVENT]
    return found if isinstance(found, dict) else {}


def _runs_on_pull_requests(path: Path) -> bool:
    return _pull_request_trigger(path) is not None


def _globs(value: object) -> re.Pattern[str] | None:
    names = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    patterns = [glob.translate(str(n), recursive=True, include_hidden=True) for n in names]
    return re.compile("|".join(f"(?:{p})" for p in patterns)) if patterns else None


def _applies(trigger: dict, base: str, changed: list[str]) -> bool:
    branches = _globs(trigger.get("branches"))
    if branches is not None and not branches.match(base):
        return False
    paths = _globs(trigger.get("paths"))
    if paths is not None and not any(paths.match(p) for p in changed):
        return False
    ignored = _globs(trigger.get("paths-ignore"))
    return ignored is None or not changed or not all(ignored.match(p) for p in changed)


def required(root: Path, base: str, candidate: ci_landing.Candidate) -> tuple[str, ...]:
    names = list(workflows(root))
    code, listed = _git(root, "diff", "--name-only", candidate.base, candidate.head)
    changed = [p for p in listed.splitlines() if p] if code == 0 else []
    folder = root / ci_landing.WORKFLOW_DIR
    for path in sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")]):
        trigger = _pull_request_trigger(path)
        if path.name in names or trigger is None:
            continue
        if code != 0 or _applies(trigger, base, changed):
            names.append(path.name)
    return tuple(names)


def workflows(root: Path) -> tuple[str, ...]:
    try:
        names = load_landing_workflows(root) or tuple(f.name for f in ci_landing.workflows(root))
    except (ValueError, ci_landing.CiLandingError) as error:
        raise PrDeliveryError(str(error)) from error
    silent = [n for n in names if not _runs_on_pull_requests(root / ci_landing.WORKFLOW_DIR / n)]
    if silent:
        raise PrDeliveryError(
            f"{', '.join(silent)} has no {EVENT} trigger, so a pull request never runs it and "
            f"nothing could prove the landing; add the trigger, or name other workflows in "
            f"{CONFIG_FILE} as [landing] workflows = [...]"
        )
    return names


def _git(root: Path, *args: str) -> tuple[int, str]:
    done = checkout.git(list(args), cwd=root, check=False)
    return done.returncode, (done.stdout or "").strip()


def _require_current(root: Path, base: str, head: str, state: str) -> None:
    if _git(root, "fetch", "-q", REMOTE, base)[0] != 0:
        raise PrDeliveryError(f"git fetch {REMOTE} {base} failed; {state}; run the merge again")
    if _git(root, "merge-base", "--is-ancestor", f"{REMOTE}/{base}", head)[0] != 0:
        raise PrDeliveryError(
            f"the lane head {head[:12]} does not contain {REMOTE}/{base}, so the pull request "
            f"would prove a tree that {base} never gets; {state}; run "
            f"git pull --ff-only in the base checkout, then run the merge again"
        )


def _push(root: Path, head: str, branch: str) -> None:
    if _git(root, "push", "-q", REMOTE, f"{head}:refs/heads/{branch}")[0] != 0:
        raise PrDeliveryError(
            f"git push of {head[:12]} to {branch} was refused; nothing is merged and nothing "
            f"was force-pushed; inspect git ls-remote {REMOTE} {branch}"
        )


def _read(gh: ci_landing.Gh, argv: list[str], doing: str) -> object:
    answer = gh(argv)
    if answer is None:
        raise PrDeliveryError(f"cannot {doing}; {ci_landing.gh_cause(gh, argv)}")
    try:
        return json.loads(answer) if answer.strip() else None
    except ValueError:
        return answer.strip()


def _open(
    gh: ci_landing.Gh, candidate: ci_landing.Candidate, base: str, branch: str
) -> tuple[str, str]:
    fields = "number,url,headRefOid"
    argv = ["pr", "list", "--head", branch, "--base", base, "--state", "open", "--json", fields]
    listed = _read(gh, argv, f"list the pull requests of {branch}")
    for found in listed if isinstance(listed, list) else []:
        if isinstance(found, dict) and found.get("headRefOid") == candidate.head:
            return str(found.get("number")), str(found.get("url"))
    title = f"{candidate.record}: land {candidate.lane}"
    body = f"Opened by basicly worktree merge {RETRY} for {candidate.record}."
    argv = ["pr", "create", "--base", base, "--head", branch, "--title", title, "--body", body]
    created = _read(gh, argv, f"open a pull request for {branch}")
    url = str(created).splitlines()[-1].strip() if created else ""
    number = url.rstrip("/").rsplit("/", 1)[-1]
    if not number.isdigit():
        raise PrDeliveryError(f"gh pr create printed no pull request URL for {branch}: {created!r}")
    return number, url


def _run(
    gh: ci_landing.Gh,
    candidate: ci_landing.Candidate,
    flow: str,
    clock: ci_landing.Clock,
    event: str = EVENT,
) -> dict:
    fields = "databaseId,url,status,conclusion,attempt"
    argv = ["run", "list", "--workflow", flow, "--commit", candidate.head, "--event", event]
    argv += ["--json", fields]
    started = clock.monotonic()
    while True:
        listed = _read(gh, argv, f"list the {flow} runs of {candidate.head[:12]}")
        if isinstance(listed, list) and listed and isinstance(listed[0], dict):
            return listed[0]
        if clock.monotonic() - started >= ci_landing.FIND_S:
            raise PrDeliveryError(
                f"no {flow} {event} run for {candidate.head[:12]} appeared; check that its "
                f"{event} trigger does not skip these paths"
            )
        clock.sleep(ci_landing.POLL_S)


def green_after_merge(
    root: Path, candidate: ci_landing.Candidate, gh: ci_landing.Gh, clock: ci_landing.Clock
) -> tuple[str, ...]:
    runs = []
    for flow in workflows(root):
        state = _finished(gh, flow, _run(gh, candidate, flow, clock, "push"), clock)
        if refused := ci_landing.verdict(state, candidate, flow, RETRY):
            raise PrDeliveryError(refused)
        runs.append(str(state.get("url")))
    return tuple(runs)


def _finished(gh: ci_landing.Gh, flow: str, run: dict, clock: ci_landing.Clock) -> dict:
    fields = "status,conclusion,attempt,url,jobs"
    argv = ["run", "view", str(run.get("databaseId")), "--json", fields]
    unread = 0
    while True:
        viewed = gh(argv)
        state = json.loads(viewed) if viewed else {}
        if isinstance(state, dict) and state.get("status") == "completed":
            return state
        unread = unread + 1 if viewed is None else 0
        if unread >= UNREAD_POLLS:
            raise PrDeliveryError(
                f"cannot read {flow} run {run.get('url')}; {ci_landing.gh_cause(gh, argv)}"
            )
        clock.sleep(ci_landing.POLL_S)


def _earlier_heads(root: Path, gh: ci_landing.Gh, candidate: ci_landing.Candidate) -> list[str]:
    argv = ["pr", "list", "--state", "all", "--limit", "200", "--json", "headRefName,headRefOid"]
    listed = _read(gh, argv, f"list the pull requests of {candidate.lane}")
    prefix = f"{BRANCH_PREFIX}/{candidate.lane}/"
    rows = listed if isinstance(listed, list) else []
    heads = {
        str(found.get("headRefOid"))
        for found in rows
        if isinstance(found, dict) and str(found.get("headRefName", "")).startswith(prefix)
    }
    heads.discard(candidate.head)
    return sorted(h for h in heads if _git(root, "cat-file", "-e", f"{h}^{{commit}}")[0] == 0)


def _require_untried(
    gh: ci_landing.Gh, candidate: ci_landing.Candidate, flow: str, base: str
) -> None:
    argv = ["run", "list", "--workflow", flow, "--commit", candidate.head, "--event", EVENT]
    listed = _read(gh, [*argv, "--json", "url,status,conclusion,attempt"], f"list {flow} runs")
    for run in listed if isinstance(listed, list) else []:
        if not isinstance(run, dict) or run.get("status") != "completed":
            continue
        if run.get("conclusion") == "success" and run.get("attempt") == 1:
            continue
        raise PrDeliveryError(
            f"{flow} run {run.get('url')} already ended {run.get('conclusion')} on attempt "
            f"{run.get('attempt')} for {candidate.head[:12]}, and a pull request runs again "
            f"only on a new head, so nothing is pushed; commit a fix in the lane, or retry "
            f"the same tree once with git commit --allow-empty or a rebase on {base}, then "
            f"run basicly worktree merge {candidate.lane} --bead {candidate.record} {RETRY}"
        )


def prove(
    root: Path,
    candidate: ci_landing.Candidate,
    base: str,
    gh: ci_landing.Gh,
    clock: ci_landing.Clock,
) -> tuple[str, str, str, tuple[str, ...]]:
    flows = required(root, base, candidate)
    _require_current(root, base, candidate.head, "nothing is pushed")
    earlier = _earlier_heads(root, gh, candidate)
    for flow in flows:
        _require_untried(gh, candidate, flow, base)
        failed = ci_landing.failed_runs(root, candidate, flow, gh, earlier)
        if refused := ci_landing.spent(failed, candidate, flow, RETRY):
            raise PrDeliveryError(f"{refused}; nothing is pushed")
    branch = branch_name(candidate.lane, candidate.head)
    _push(root, candidate.head, branch)
    number, url = _open(gh, candidate, base, branch)
    runs = []
    for flow in flows:
        state = _finished(gh, flow, _run(gh, candidate, flow, clock), clock)
        if refused := ci_landing.verdict(state, candidate, flow, RETRY):
            raise PrDeliveryError(f"{refused}; pull request {url} stays open")
        runs.append(str(state.get("url")))
    return number, url, branch, tuple(runs)


def _merged_commit(gh: ci_landing.Gh, number: str) -> str:
    viewed = _read(gh, ["pr", "view", number, "--json", "state,mergeCommit"], f"read #{number}")
    state = viewed if isinstance(viewed, dict) else {}
    commit = state.get("mergeCommit")
    oid = commit.get("oid") if isinstance(commit, dict) else None
    if state.get("state") != "MERGED" or not isinstance(oid, str):
        raise PrDeliveryError(f"pull request #{number} is {state.get('state')}, not MERGED")
    return oid


def deliver(
    root: Path,
    candidate: ci_landing.Candidate,
    base: str,
    gh: ci_landing.Gh = gh_cli.run,
    clock: ci_landing.Clock | None = None,
) -> Landed:
    clock = clock or ci_landing.Clock()
    number, url, branch, runs = prove(root, candidate, base, gh, clock)
    _require_current(root, base, candidate.head, f"nothing is merged and {url} stays open")
    subject, body = merge_message(candidate.record, number, branch, base)
    argv = ["pr", "merge", number, "--merge", "--match-head-commit", candidate.head]
    _read(gh, [*argv, "--subject", subject, "--body", body], f"merge pull request {url}")
    commit = _merged_commit(gh, number)
    _require_fetched(root, base, commit)
    tree = _git(root, "rev-parse", f"{candidate.head}^{{tree}}")[1]
    if _git(root, "rev-parse", f"{commit}^{{tree}}")[1] != tree:
        raise PrDeliveryError(
            f"GitHub merged {url} as {commit[:12]}, whose tree differs from the proved tree "
            f"{tree[:12]}; no landing evidence is written; run basicly verify --mode full "
            f"--issue {candidate.record} on {base}"
        )
    tracker.add_artifact(
        root,
        candidate.record,
        "landing-verification",
        {
            "status": "ci-verified",
            "head": commit,
            "base": base,
            "run": " ".join(runs),
            "tree": tree,
        },
    )
    notes = [_fast_forward(root, base), *sweep(root, candidate.lane, base, gh, url)]
    return Landed(commit, url, runs, tuple(note for note in notes if note))


def branches(root: Path, lane: str) -> tuple[str, ...]:
    code, listed = _git(
        root, "ls-remote", "--heads", REMOTE, f"refs/heads/{BRANCH_PREFIX}/{lane}/*"
    )
    if code != 0:
        return ()
    heads = (line.split()[-1] for line in listed.splitlines() if line.strip())
    return tuple(sorted(head.removeprefix("refs/heads/") for head in heads))


def _close_open(gh: ci_landing.Gh, branch: str, base: str, merged: str) -> str:
    argv = [
        "pr",
        "list",
        "--head",
        branch,
        "--base",
        base,
        "--state",
        "open",
        "--json",
        "number,url",
    ]
    try:
        listed = _read(gh, argv, f"list the open pull requests of {branch}")
        closed = []
        for found in listed if isinstance(listed, list) else []:
            number = str(found.get("number")) if isinstance(found, dict) else ""
            comment = (
                f"Superseded by {merged}." if merged else "Superseded: the lane was cleaned up."
            )
            _read(gh, ["pr", "close", number, "--comment", comment], f"close #{number}")
            closed.append(f"closed pull request #{number}")
    except PrDeliveryError as error:
        return f"kept {REMOTE}/{branch}: {error}"
    return "; ".join(closed)


def sweep(
    root: Path, lane: str, base: str, gh: ci_landing.Gh = gh_cli.run, merged: str = ""
) -> list[str]:
    notes = []
    for branch in branches(root, lane):
        closed = _close_open(gh, branch, base, merged)
        if closed.startswith("kept"):
            notes.append(closed)
            continue
        deleted = remote_branches.delete_landed(root, branch, base, unlanded_paths)
        notes.append("; ".join(note for note in (closed, deleted) if note))
    return [note for note in notes if note]


def _require_fetched(root: Path, base: str, commit: str) -> None:
    _git(root, "fetch", "-q", REMOTE, base)
    if _git(root, "merge-base", "--is-ancestor", commit, f"{REMOTE}/{base}")[0] != 0:
        raise PrDeliveryError(
            f"{REMOTE}/{base} does not hold the merge commit {commit[:12]} after git fetch; "
            f"no landing evidence is written; run git fetch {REMOTE} and check the pull request"
        )


def _fast_forward(root: Path, base: str) -> str:
    if checkout.current_branch(root) != base:
        return ""
    if _git(root, "merge", "--ff-only", "-q", f"{REMOTE}/{base}")[0] != 0:
        return f"{base} here did not fast-forward to {REMOTE}/{base}; run git pull --ff-only"
    return ""
