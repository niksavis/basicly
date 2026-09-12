from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import roles, tracker

MARKER = "[harness-review]"
_LENS_PREFIX = "lens="


@dataclass(frozen=True)
class LensFindings:
    lens: str
    findings: str = ""


def _marker_body(lens: str, findings: str) -> str:
    return f"{MARKER} {_LENS_PREFIX}{lens}\n{findings.strip()}"


def record(repo_root: Path, issue_id: str, lens: str, findings: str) -> None:

    if not findings.strip():
        return
    tracker.add_comment(repo_root, issue_id, _marker_body(lens, findings))


def _parse_marker(text: str) -> tuple[str, str] | None:

    head, _, body = text.strip().partition("\n")
    fields = head.split()
    if len(fields) != 2 or fields[0] != MARKER or not fields[1].startswith(_LENS_PREFIX):
        return None
    return fields[1][len(_LENS_PREFIX) :], body.strip()


def latest_per_lens(repo_root: Path, issue_id: str) -> tuple[LensFindings, ...]:

    latest: dict[str, str] = {}
    for comment in tracker.try_read_comments(repo_root, issue_id):
        parsed = _parse_marker(str(comment.get("text", "")))
        if parsed is not None:
            latest[parsed[0]] = parsed[1]
    return tuple(LensFindings(lens, latest.get(lens, "")) for lens in roles.REVIEW_LENSES)
