"""One lens review's findings, as recorded on the unit that was reviewed.

The boundary is *what a reviewer said* against *what its dispatch cost*:
:mod:`basicly.run_record` prices a dispatch, redacts its prompt and keeps no reply, so
without this the VALIDATE fan-out would spend a dispatch per lens and drop every
answer. Split from :mod:`basicly.roles`, which owns the vocabulary, because the layer
stack puts ``roles`` below the tracker seam and a recorder must reach it.

**One comment per lens, and that is the contract.** factory-loop.md §6.4 closes with
the rule that lens output is reported per lens and never merged into one ranked list,
since a change can pass one axis and fail another and a merged ranking lets the strong
axis mask the weak one. No call shape here merges two lenses: :func:`record` takes one,
and :func:`latest_per_lens` hands back one :class:`LensFindings` per lens in vocabulary
order — a shape a consumer has to render as sections, carrying no field to rank by.

Transport is the ``[harness-review]`` marker family, a sibling of ``[harness-policy]``
and ``[harness-artifact]``, through the :func:`basicly.tracker.add_comment` seam they share.
"""

from __future__ import annotations

# comment-density-waiver: cohesion: 11 lines of code carrying the §6.4 contract they exist to
# satisfy, the layer fact that forced the split off `roles`, and why an empty reply and
# a repeat review are handled the opposite way to `[harness-artifact]`. Cutting to 50%
# would leave a recorder whose per-lens shape reads as a formatting choice — which is
# the finding the next reader would have to re-derive to know it must not be merged.
from dataclasses import dataclass
from pathlib import Path

from . import roles, tracker

# The marker family this module owns, and how the lens is carried ahead of the text.
MARKER = "[harness-review]"
_LENS_PREFIX = "lens="


@dataclass(frozen=True)
class LensFindings:
    """What one lens reported about one unit, under that lens's own name.

    There is no severity, rank or position field, and that is the §6.4 rule expressed
    as a type: a consumer holding two of these has nothing to sort them by, so the only
    thing it can do with them is report two sections.

    *findings* is empty when the lens recorded nothing, which is a different fact from
    the lens being absent — :func:`latest_per_lens` returns the entry either way.
    """

    lens: str
    findings: str = ""


def _marker_body(lens: str, findings: str) -> str:
    """One lens review as the marker line that carries it (pure)."""
    return f"{MARKER} {_LENS_PREFIX}{lens}\n{findings.strip()}"


def record(repo_root: Path, issue_id: str, lens: str, findings: str) -> None:
    """Record *findings* on *issue_id* under *lens*; an empty reply records nothing.

    A reviewer that found nothing on its axis says so in one line, which is text like
    any other. A reviewer that answered nothing at all is a dispatch that failed, and a
    blank marker would read to the next reader as a lens that ran and was clean.

    Appends rather than deduplicating, unlike :func:`artifact_record.write`: re-entering
    VALIDATE re-dispatches, and two reviews of one lens are two answers about two trees,
    so collapsing them on equality would drop the one that saw the repair.

    Raises:
        RuntimeError: the marker did not reach the authoritative store.
    """
    if not findings.strip():
        return
    tracker.add_comment(repo_root, issue_id, _marker_body(lens, findings))


def _parse_marker(text: str) -> tuple[str, str] | None:
    """The lens and findings *text* carries, or None when it is not a review (pure).

    Token-exact on the first line, like :func:`policy._marker_matches` and for the same
    reason: a prefix match would read a sibling family's marker as a review with a
    strange lens name.
    """
    head, _, body = text.strip().partition("\n")
    fields = head.split()
    if len(fields) != 2 or fields[0] != MARKER or not fields[1].startswith(_LENS_PREFIX):
        return None
    return fields[1][len(_LENS_PREFIX) :], body.strip()


def latest_per_lens(repo_root: Path, issue_id: str) -> tuple[LensFindings, ...]:
    """*issue_id*'s most recent review on each declared lens, one entry per lens.

    In :data:`roles.REVIEW_LENSES` order, which is the vocabulary's own and deliberately
    not a ranking (§6.4). Every declared lens gets an entry whether or not it recorded
    anything, so a reader can tell an axis that ran and was clean from one that never
    answered; a marker naming a lens outside the vocabulary is dropped, because the
    vocabulary is what defines the set being reported.

    The latest per lens rather than every one: :func:`record` appends, so a lens that
    reviewed twice reviewed two different trees, and the earlier answer judged work a
    repair has already been through. Same reason the repair brief is consumed on read.

    Soft (:func:`tracker.try_read_comments`): a review is advisory evidence, so a store that
    cannot answer costs a repair its findings and can never cost a gate its verdict.
    """
    latest: dict[str, str] = {}
    for comment in tracker.try_read_comments(repo_root, issue_id):
        parsed = _parse_marker(str(comment.get("text", "")))
        if parsed is not None:
            latest[parsed[0]] = parsed[1]
    return tuple(LensFindings(lens, latest.get(lens, "")) for lens in roles.REVIEW_LENSES)
