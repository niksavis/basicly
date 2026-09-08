"""The INVEST conditions a dispatch can check on a record body (basicly-q1ve1fn).

Reads; never decides. :func:`policy.definition_of_ready` owns the verdict and
:mod:`basicly.loop` owns the refusal.

**Valuable is a trigger, not a persona.** Wake's INVEST asks only that a story be
"valuable to the customer" and names no role, so either voice satisfies it:

* a job story - ``When <situation>, I want to <motivation>, so I can <outcome>.``
* a user story - ``As a <persona>, I want <goal>, so that <benefit>.``

Matching the persona shape alone would refuse every job story and teach an author to
prepend a persona the work has no one for. Patching pins the case: a published
vulnerability triggers it and no person appears.

**Every work type owes a trigger.** An exemption would key on ``issue_type``, which the
gated author writes, so it is an escape hatch rather than a rule.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from .config import DEFAULT_TYPE_SECTIONS, load_type_sections
from .plan_record import ACCEPTANCE_HEADING, has_heading, section_entries

TRIGGER_HEADING = "## Trigger"

# Stated once and quoted wherever an author is told to write one, because two copies of
# an example are two things to drift.
JOB_STORY_EXAMPLE = "When <situation>, I want to <motivation>, so I can <outcome>."
USER_STORY_EXAMPLE = "As a <persona>, I want <goal>, so that <benefit>."

# Bounded, not greedy: under DOTALL an unbounded `.+?` pairs a `when` with an `i want`
# three thousand characters away, so a body stating no trigger matches one by accident.
_SPAN = 400
_JOB_STORY = re.compile(
    rf"\bwhen\b.{{0,{_SPAN}}}?\bi want\b.{{0,{_SPAN}}}?\bso (?:i|we) can\b",
    re.IGNORECASE | re.DOTALL,
)
_USER_STORY = re.compile(
    rf"\bas an?\b.{{0,200}}?\bi want\b.{{0,{_SPAN}}}?\bso that\b",
    re.IGNORECASE | re.DOTALL,
)

# The scaffold must show the shape, so its hint necessarily matches the patterns above.
# Only this marker tells the example from a statement.
_PLACEHOLDER = re.compile(r"<[^>]+>|\bTODO\b")


def trigger_voice(description: str) -> str | None:
    """``"job"`` or ``"user"`` for the voice *description* states a trigger in, else None.

    Searched over the whole body, not under :data:`TRIGGER_HEADING`: 223 of 306 active
    records carried such a heading when this was measured and one carried a trigger, so
    gating on the heading would gate on nothing.
    """
    for voice, pattern in (("job", _JOB_STORY), ("user", _USER_STORY)):
        for match in pattern.finditer(description):
            if not _PLACEHOLDER.search(match.group(0)):
                return voice
    return None


def trigger_sentence(description: str) -> str:
    """The trigger *description* states, verbatim, or ``""``.

    A decomposed child inherits its parent's, because the situation that triggered the
    feature is the situation that triggers each slice of it. Without inheritance the
    decomposer would write an unfilled placeholder and every child would be refused at
    its own classify gate.
    """
    for pattern in (_JOB_STORY, _USER_STORY):
        for match in pattern.finditer(description):
            if not _PLACEHOLDER.search(match.group(0)):
                return match.group(0).strip()
    return ""


def trigger_remedy() -> str:
    """The refusal's remedy: both voices offered, neither demanded."""
    return (
        f"state the trigger in either voice - a situation, {JOB_STORY_EXAMPLE!r}, or a "
        f"persona, {USER_STORY_EXAMPLE!r}. A persona is never required: where a situation "
        "triggers the work and no person wants it, inventing a persona is the defect."
    )


def required_conditions(work_type: str, repo_root: Path | None = None) -> tuple[str, ...]:
    """Every body section a dispatch requires for *work_type*, in Jeffries' three-C order.

    Conversation is the trigger and Confirmation the criteria; a per-type section sits
    between them as that type's own evidence, read from *repo_root*'s
    ``[policy.type_sections]`` so a repository changes it without a code change.
    """
    declared = DEFAULT_TYPE_SECTIONS if repo_root is None else load_type_sections(repo_root)
    return (TRIGGER_HEADING, *declared.get(work_type, ()), ACCEPTANCE_HEADING)


def missing_sections(record: Mapping[str, object], required: Sequence[str]) -> tuple[str, ...]:
    """Every section in *required* that *record* does not satisfy.

    The two sections this module owns are checked for what they *say*; the rest keep the
    older heading-only rule. That asymmetry is the fix - a heading with nothing under it
    used to clear the gate.
    """
    described = record.get("description")
    body = described if isinstance(described, str) else ""
    checks = {
        TRIGGER_HEADING: lambda: trigger_voice(body) is not None,
        ACCEPTANCE_HEADING: lambda: _states_acceptance(record, body),
    }
    return tuple(
        section
        for section in required
        if not checks.get(section, lambda s=section: has_heading(body, s))()
    )


def _states_acceptance(record: Mapping[str, object], body: str) -> bool:
    """Whether *record* states a criterion in either legitimate carrier (basicly-58iu).

    Reading only the body put 82 records that state their criteria into a report of
    records that state none.
    """
    field = record.get("acceptance_criteria")
    if isinstance(field, str) and _states_something(field):
        return True
    return any(_states_something(entry) for entry in section_entries(body, ACCEPTANCE_HEADING))


def _states_something(text: str) -> bool:
    """Whether *text* is content rather than an unfilled placeholder."""
    return bool(text.strip()) and not _PLACEHOLDER.search(text)
