from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import checkout, ci_landing, delivery_pr, gh_cli

REMOTE = "origin"


@dataclass(frozen=True)
class Release:
    tag: str
    date: str
    record: str


def lane_name(tag: str) -> str:
    return f"release-{tag}"


def _git(root: Path, *args: str) -> tuple[int, str]:
    done = checkout.git(list(args), cwd=root, check=False)
    return done.returncode, (done.stdout or "").strip()


def deliver(
    root: Path,
    cut: Release,
    gh: ci_landing.Gh = gh_cli.run,
    clock: ci_landing.Clock | None = None,
) -> list[str]:
    tag, date, record = cut.tag, cut.date, cut.record
    base = checkout.current_branch(root)
    head = _git(root, "rev-parse", "HEAD")[1]
    destination = _git(root, "rev-parse", f"{REMOTE}/{base}")[1]
    candidate = ci_landing.Candidate(lane_name(tag), record, destination, head)
    landed = delivery_pr.deliver(root, candidate, base, gh, clock)
    if _git(root, "merge-base", "--is-ancestor", landed.commit, f"{REMOTE}/{base}")[0] != 0:
        raise delivery_pr.PrDeliveryError(
            f"{REMOTE}/{base} does not hold the merge commit {landed.commit[:12]}, so {tag} is "
            f"not created; run git fetch {REMOTE} and check {landed.pull_request}"
        )
    if _git(root, "tag", "-a", tag, "-m", f"{tag} ({date})", landed.commit)[0] != 0:
        raise delivery_pr.PrDeliveryError(
            f"{landed.pull_request} merged as {landed.commit[:12]}, but git tag {tag} was "
            f"refused; check git tag --list {tag}, then tag that commit"
        )
    if _git(root, "push", "-q", REMOTE, f"refs/tags/{tag}")[0] != 0:
        raise delivery_pr.PrDeliveryError(
            f"{tag} is tagged here on {landed.commit[:12]}, but git push of the tag was refused; "
            f"nothing was force-pushed; run git push {REMOTE} refs/tags/{tag}"
        )
    return [
        f"{landed.pull_request} merged as {landed.commit[:12]}; tagged {tag} on it and pushed "
        "the tag",
        *landed.notes,
    ]
