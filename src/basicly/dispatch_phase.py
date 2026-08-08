"""The phase a dispatch is recorded under, and which of them is a lane's own work.

One vocabulary, in one place, because it had none: every writer spelled its phase
as a string literal at the call site and every reader filtered on its own idea of
what a lane was — and the two filtered oppositely (basicly-tcmy.5).
``loop._run_agent`` records the interactive build dispatch as :data:`BUILD_PHASE`
and ``supervise._dispatch_lane`` records the supervised one as :data:`LANE_PHASE`.
They are the same kind of work — an agent turned loose on a worktree to do a
node's build — so a consumer wanting "what a lane costs" wants both.
``decompose.unsized_lane_tokens`` required ``lane`` alone, which on this repo's own
history bounded a lane from 24 samples while 128 records of the interactive path
(the documented default) were invisible to it.

The helper phases are the other half of the same defect: a rubric judge and a
decider read and answer, neither writes code, and a calibration that samples them
measures the cost of a helper and reports it as the cost of the work.

A leaf by construction — it imports nothing and answers about a string, so both
the module that persists a dispatch (:mod:`basicly.run_record`) and the one that
prices it (:mod:`basicly.spend_calibration`) can hold the same definition rather
than each carrying a copy. Split out of ``run_record`` when the module-size
ratchet caught that module growing; the boundary is *the name* against *the
record*, and nothing here reads a dispatch.
"""

from __future__ import annotations

BUILD_PHASE = "build"  # loop._run_agent — the interactive path
LANE_PHASE = "lane"  # supervise._dispatch_lane — the supervised path
VALIDATE_PHASE = "validate"  # rubrics — a read-only judge
DECIDE_PHASE = "decide"  # decisions — the decider answering a queued item
PROPOSE_PHASE = "propose"  # loop._run_proposer — originating a phase's input

# The dispatches that are an agent doing a node's work. One definition, read by
# both the unsizeable-lane bound and the spend calibration, so the two can no
# longer disagree about what a lane is.
WRITE_PHASES = frozenset({BUILD_PHASE, LANE_PHASE})


def is_write_phase(phase: object) -> bool:
    """True when *phase* names a write dispatch (:data:`WRITE_PHASES`).

    Takes the raw recorded value rather than a ``str``: the caller is reading a
    persisted record, where the key may be absent, null (every dispatch recorded
    before the field existed) or externally tampered. All of those answer False —
    a phase that cannot be read is not evidence that a lane ran.
    """
    return isinstance(phase, str) and phase in WRITE_PHASES
