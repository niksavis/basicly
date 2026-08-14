"""One lens review's findings, as recorded on the unit that was reviewed.

The boundary is *what a reviewer said* against *what its dispatch cost*:
:mod:`basicly.run_record` prices a dispatch, redacts its prompt and keeps no reply, so
without this the VALIDATE fan-out would spend a dispatch per lens and drop every
answer. Split from :mod:`basicly.roles`, which owns the vocabulary, because the layer
stack puts ``roles`` below ``br`` and a recorder must reach the tracker.

**One comment per lens, and that is the contract.** factory-loop.md §6.4 closes with
the rule that lens output is reported per lens and never merged into one ranked list,
since a change can pass one axis and fail another and a merged ranking lets the strong
axis mask the weak one. No call shape here takes two lenses, so the rule holds by
construction rather than by instruction.

Transport is the ``[harness-review]`` marker family, a sibling of ``[harness-policy]``
and ``[harness-artifact]``, through the :func:`basicly.br.add_comment` seam they share.
"""

from __future__ import annotations

# comment-density-waiver: 11 lines of code carrying the §6.4 contract they exist to
# satisfy, the layer fact that forced the split off `roles`, and why an empty reply and
# a repeat review are handled the opposite way to `[harness-artifact]`. Cutting to 50%
# would leave a recorder whose per-lens shape reads as a formatting choice — which is
# the finding the next reader would have to re-derive to know it must not be merged.
from pathlib import Path

from . import br

# The marker family this module owns, and how the lens is carried ahead of the text.
MARKER = "[harness-review]"
_LENS_PREFIX = "lens="


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
    br.add_comment(repo_root, issue_id, _marker_body(lens, findings))
