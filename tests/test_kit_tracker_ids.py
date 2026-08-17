"""Tests for the tracker kit's id minting (basicly-vkh0.12).

Three kinds of test carry the three acceptance criteria, and each is written to fail
for a reason rather than to restate the code:

- **The collision budget is derived, not asserted.** The declared target and the table
  it produces live in the module docstring; these tests parse that table and recompute
  every row from the birthday bound, then cross-check the exponential approximation
  against the exact product ``1 - Π(1 - i/N)``. A row edited in either place without
  the other breaks here.
- **The no-slug rule is tripped, not read.** The commit-message gate's own ``validate``
  is loaded from ``.basicly/core/hooks/beads-commit-msg.py`` and called on minted ids,
  with the hyphenated id as the positive control — so "a hyphen breaks the gate" is a
  demonstrated mechanism and the pass on our own ids means something.
- **The evidence id is held to the shipped implementations.** ``decision_id_for``,
  ``marker_id`` and ``cost_marker_id`` are imported and compared id-for-id, because a
  kit that cannot import them can only drift from them (the drift-gate pattern
  ``tests/test_kit_resolver.py`` uses for the model map).

Randomness is injected everywhere it matters: a seeded :class:`random.Random` makes a
mint reproducible, and the two scripted sources below force the collision-retry and
exhaustion paths that a real draw would reach roughly never.
"""

from __future__ import annotations

import ast
import importlib.util
import random
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from basicly.decisions import decision_id_for
from basicly.run_record import cost_marker_id, marker_id

REPO_ROOT = Path(__file__).parent.parent
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker" / "ids.py"
GATE = REPO_ROOT / ".basicly" / "core" / "hooks" / "beads-commit-msg.py"

# The declared thresholds of the module docstring's table, retyped here on purpose:
# this is the change detector the acceptance criterion asks for, so moving the target
# has to be a deliberate edit in two places rather than a silent re-derivation.
DECLARED_THRESHOLDS = ((4, 18), (5, 109), (6, 659), (7, 3958))

_TABLE_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|$", re.MULTILINE)


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way a consumer without basicly would."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ids = _load(KIT, "tracker_ids")
gate = _load(GATE, "beads_commit_msg_gate")


class _ScriptedRandom(random.Random):
    """Yields the characters of *roots* in order, then draws for real.

    The point is to make a *specific* candidate come up first — a taken id, a
    tombstoned one — so the discard branch runs deterministically instead of waiting on
    a 1-in-a-million draw. Falling back to a genuine draw once the script is spent
    keeps the mint able to finish.
    """

    def __init__(self, roots: list[str], seed: int = 7) -> None:
        super().__init__(seed)
        self._scripted = [char for root in roots for char in root]
        self.consumed = 0

    def choice(self, seq):  # type: ignore[override]
        """Return the next scripted character, or a real draw when spent."""
        if self.consumed < len(self._scripted):
            char = self._scripted[self.consumed]
            self.consumed += 1
            return char
        return super().choice(seq)


class _ConstantRandom(random.Random):
    """Always draws the first character of the alphabet, so every candidate collides."""

    def choice(self, seq):  # type: ignore[override]
        """Return the alphabet's first character."""
        return seq[0]


def _exact_collision_probability(population: int, length: int) -> float:
    """``1 - Π(1 - i/N)`` — the birthday probability without the approximation."""
    space = float(ids.RADIX**length)
    survival = 1.0
    for occupied in range(population):
        survival *= 1.0 - occupied / space
    return 1.0 - survival


def _filler(count: int, prefix: str = "basicly") -> set[str]:
    """*count* distinct minted ids, shaped so they cannot collide with a real draw."""
    return {f"{prefix}-r{index}" for index in range(count)}


# --- the kit constraint: no basicly, standard library only, no clock ------------


def test_the_kit_imports_nothing_but_the_standard_library() -> None:
    """A basicly or third-party import would break the kit in a consumer repo."""
    tree = ast.parse(KIT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import would make the kit need a package"
            if node.module:
                imported.add(node.module.split(".")[0])
    assert imported, "the AST walk found no imports at all, so it proves nothing"
    assert "basicly" not in imported
    assert imported <= set(sys.stdlib_module_names), sorted(imported - set(sys.stdlib_module_names))


def test_the_kit_reads_no_clock() -> None:
    """Design §9.5: a timestamp is evidence, so no id may be a function of one."""
    tree = ast.parse(KIT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert {"time", "datetime", "calendar", "zoneinfo"}.isdisjoint(imported), sorted(imported)


# --- AC 1: the collision budget is declared, derived, and checkable -------------


def _recorded_derivation() -> str:
    """The module docstring, where the declared target and its table are recorded."""
    doc = ids.__doc__
    assert doc, "the derivation is recorded in the module docstring; there isn't one"
    return doc


def test_the_declared_target_is_the_one_the_docstring_states() -> None:
    """The prose a reader checks and the constant a mint uses are the same number."""
    assert ids.MAX_COLLISION_PROBABILITY == 1e-4
    assert "1e-4" in _recorded_derivation()
    assert "P(collision) ≈ 1 - e^(-n² / 2N)" in _recorded_derivation()


def test_the_docstring_table_is_what_the_birthday_bound_derives() -> None:
    """Every row of the recorded derivation is recomputed, so neither side can drift."""
    rows = _TABLE_ROW.findall(_recorded_derivation())
    assert len(rows) == len(DECLARED_THRESHOLDS), rows
    for (length, space, population), (declared_length, declared_population) in zip(
        rows, DECLARED_THRESHOLDS, strict=True
    ):
        assert int(length) == declared_length
        assert int(space.replace(",", "")) == ids.RADIX**declared_length
        assert int(population.replace(",", "")) == declared_population
        assert ids.max_population(declared_length) == declared_population


def test_the_declared_population_is_the_last_one_inside_the_budget() -> None:
    """Each threshold is a boundary: one more root breaks the declared target."""
    for length, population in DECLARED_THRESHOLDS:
        assert ids.collision_probability(population, length) <= ids.MAX_COLLISION_PROBABILITY
        assert ids.collision_probability(population + 1, length) > ids.MAX_COLLISION_PROBABILITY


def test_the_approximation_is_the_conservative_side_of_the_exact_probability() -> None:
    """``1 - e^(-n²/2N)`` over-states the risk it approximates, so sizing on it is safe."""
    for length, population in DECLARED_THRESHOLDS:
        approximate = ids.collision_probability(population, length)
        exact = _exact_collision_probability(population, length)
        assert approximate >= exact, (length, population, approximate, exact)
        assert approximate - exact < 0.1 * exact, (length, population, approximate, exact)


def test_root_length_changes_at_the_declared_population_thresholds() -> None:
    """The acceptance criterion itself: the length moves 4 → 5 → 6 → 7 where declared."""
    assert ids.root_length_for(0) == ids.MIN_ROOT_LENGTH == 4
    for length, population in DECLARED_THRESHOLDS:
        assert ids.root_length_for(population) == length
        assert ids.root_length_for(population + 1) == length + 1


def test_a_tighter_target_lengthens_ids_at_the_same_population() -> None:
    """The length is a function of the declared target, not a constant beside it."""
    assert ids.root_length_for(18, target=1e-4) == 4
    # 18 roots at 1e-6 need N >= 18²/2e-6 = 1.62e8, past 36**5 = 6.0e7, so six.
    assert ids.root_length_for(18, target=1e-6) == 6
    assert ids.max_population(5, target=1e-6) == 10


def test_a_population_beyond_the_longest_root_raises_rather_than_capping() -> None:
    """A silent cap would serve an id outside the budget it claims to honour."""
    with pytest.raises(ids.IdSpaceExhaustedError):
        ids.root_length_for(10**9)


# --- AC 2: a record id is opaque, stable, never reused --------------------------


def test_a_minted_record_id_is_opaque_and_well_formed() -> None:
    """The default path — a real SystemRandom draw — produces a gate-shaped id."""
    record = ids.mint_root_id("basicly", frozenset())
    assert ids.is_record_id(record)
    assert record.startswith("basicly-")
    root = record.split("-", 1)[1]
    assert len(root) == 4
    assert set(root) <= set(ids.ALPHABET)


def test_a_mint_is_reproducible_from_its_injected_randomness() -> None:
    """Nothing but (prefix, minted, rng) decides the id — no clock, no counter, no state."""
    taken = _filler(3)
    first = ids.mint_root_id("basicly", taken, rng=random.Random(11))
    second = ids.mint_root_id("basicly", taken, rng=random.Random(11))
    assert first == second


def test_a_minted_root_lengthens_as_the_ledger_crosses_a_threshold() -> None:
    """Length is derived from the live population at mint time, per the table."""
    for length, population in DECLARED_THRESHOLDS:
        # One short of the threshold: this mint is the population'th root.
        at_threshold = ids.mint_root_id("basicly", _filler(population - 1), rng=random.Random(3))
        assert len(at_threshold.split("-", 1)[1]) == length
        # One past it: the same budget now demands another character.
        past_threshold = ids.mint_root_id("basicly", _filler(population), rng=random.Random(3))
        assert len(past_threshold.split("-", 1)[1]) == length + 1


def test_adaptive_length_never_alters_an_id_already_minted() -> None:
    """A longer root for new ids, never a rewrite of an old one."""
    early = ids.mint_root_id("basicly", frozenset(), rng=random.Random(5))
    assert len(early.split("-", 1)[1]) == 4
    grown = _filler(109) | {early}
    later = ids.mint_root_id("basicly", grown, rng=random.Random(5))
    assert len(later.split("-", 1)[1]) == 6
    # The short id is untouched, still valid, and still spent.
    assert early in grown
    assert ids.is_record_id(early)
    assert later != early
    assert ids.next_child_id(early, grown) == f"{early}.1"


def test_a_thousand_mints_never_repeat_an_id() -> None:
    """Each id is fed back in, so a repeat would have to survive the taken check."""
    rng = random.Random(23)
    minted: set[str] = set()
    for _ in range(1000):
        record = ids.mint_root_id("basicly", minted, rng=rng)
        assert record not in minted
        minted.add(record)
    assert len(minted) == 1000


def test_a_taken_root_that_comes_up_is_discarded() -> None:
    """The retry branch, forced: the first draw is an id the ledger already holds."""
    taken_root = "abcd"
    taken = {f"basicly-{taken_root}"}
    rng = _ScriptedRandom([taken_root])
    record = ids.mint_root_id("basicly", taken, rng=rng)
    assert record != f"basicly-{taken_root}"
    assert rng.consumed == len(taken_root), "the scripted candidate was never drawn"
    # Positive control: with nothing taken, that same script mints exactly that id —
    # so the assertion above is about the discard, not about the script.
    assert ids.mint_root_id("basicly", frozenset(), rng=_ScriptedRandom([taken_root])) == (
        f"basicly-{taken_root}"
    )


def test_a_tombstoned_id_is_never_handed_out_again() -> None:
    """A delete leaves a tombstone, and a tombstone is still a spent id."""
    deleted = "basicly-dead"
    space = ids.minted_ever(live={"basicly-liv0"}, tombstoned={deleted})
    assert deleted in space
    rng = _ScriptedRandom(["dead"])
    record = ids.mint_root_id("basicly", space, rng=rng)
    assert record != deleted
    assert rng.consumed == 4, "the tombstoned root was never offered to the mint"


def test_a_tombstoned_child_index_is_never_reused() -> None:
    """The child suffix is monotonic over every index ever used, not over live ones."""
    parent = "basicly-ab12"
    live = {parent, f"{parent}.1", f"{parent}.3"}
    tombstoned = {f"{parent}.2"}
    space = ids.minted_ever(live, tombstoned)
    assert ids.next_child_id(parent, space) == f"{parent}.4"
    # Deleting the highest child does not lower the next index either.
    after_delete = ids.minted_ever(live - {f"{parent}.3"}, tombstoned | {f"{parent}.3"})
    assert ids.next_child_id(parent, after_delete) == f"{parent}.4"


def test_a_child_id_nests_under_a_child() -> None:
    """Any record id is a valid parent, which is what the dotted shape buys."""
    assert ids.next_child_id("basicly-ab12", {"basicly-ab12"}) == "basicly-ab12.1"
    assert ids.next_child_id("basicly-ab12.4", {"basicly-ab12.4"}) == "basicly-ab12.4.1"
    assert ids.next_child_id("basicly-ab12.4", {"basicly-ab12.4.1"}) == "basicly-ab12.4.2"


def test_a_non_ascii_child_index_is_not_read_as_a_number() -> None:
    """``str.isdigit`` is true for '٣' and '²'; a child index has to be ASCII."""
    parent = "basicly-ab12"
    assert ids.next_child_id(parent, {f"{parent}.٣", f"{parent}.1"}) == f"{parent}.2"


def test_the_population_counts_roots_not_records() -> None:
    """Children share a root, so counting records would over-lengthen every id."""
    parent = "basicly-ab12"
    children = {f"{parent}.{index}" for index in range(1, 60)}
    assert ids.root_ids(children | {parent}, "basicly") == frozenset({"ab12"})
    # 59 children and one root: still inside the 4-character budget.
    assert len(ids.mint_root_id("basicly", children | {parent}).split("-", 1)[1]) == 4


def test_an_evidence_id_is_not_part_of_our_population() -> None:
    """The two kinds share a set at the caller's peril; only records size the budget."""
    record = "basicly-ab12"
    evidence = {ids.evidence_id(record, "found-info", f"fact {index}") for index in range(200)} | {
        ids.evidence_id(record, family="cost", content=None)
    }
    assert ids.root_ids(evidence | {record}, "basicly") == frozenset({"ab12"})
    assert len(ids.mint_root_id("basicly", evidence | {record}).split("-", 1)[1]) == 4


def test_another_prefix_is_not_part_of_our_population() -> None:
    """Ids under a different prefix cannot collide with ours, so they do not count."""
    foreign = {f"dev-r{index}" for index in range(200)}
    assert ids.root_ids(foreign, "basicly") == frozenset()
    assert len(ids.mint_root_id("basicly", foreign).split("-", 1)[1]) == 4


def test_a_mint_with_no_free_candidate_raises_rather_than_reusing() -> None:
    """Exhaustion is an error; handing back a spent id would corrupt the ledger."""
    taken = {f"basicly-{'0' * length}" for length in range(4, ids.MAX_ROOT_LENGTH + 1)}
    with pytest.raises(ids.IdSpaceExhaustedError):
        ids.mint_root_id("basicly", taken, rng=_ConstantRandom())


def test_next_child_id_refuses_a_parent_that_is_not_a_record_id() -> None:
    """A malformed parent would mint a child no reader could resolve."""
    with pytest.raises(ids.IdError):
        ids.next_child_id("basicly-my-slug", set())


# --- AC 3: evidence ids are content-derived, and no id carries a slug -----------


def test_the_evidence_id_reproduces_decision_id_for() -> None:
    """The kit cannot import it, so the copy is checked against it (kjc5 drift gate)."""
    cases = [
        ("basicly-vkh0", "scope", "which files does this touch?", 1),
        ("basicly-vkh0.12", "design", "opaque or derived?", 1),
        ("basicly-vkh0.12", "design", "opaque or derived?", 3),
    ]
    for record, kind, question, generation in cases:
        assert ids.evidence_id(record, kind, question, generation=generation) == decision_id_for(
            record, kind, question, generation
        )


def test_the_evidence_id_reproduces_the_dispatch_marker_id() -> None:
    """A dispatch marker keys on (phase, prompt digest, attempt) — same recipe, same id."""
    cases = [
        ("basicly-vkh0.12", "deadbeef", "build", 1),
        ("basicly-vkh0.12", "deadbeef", "build", 2),
        ("basicly-vkh0.12", "deadbeef", "validate", 1),
    ]
    for record, prompt_sha256, phase, attempt in cases:
        assert ids.evidence_id(
            record, phase, prompt_sha256, family="run", generation=attempt
        ) == marker_id(record, prompt_sha256, phase, attempt)


def test_the_evidence_id_reproduces_the_singleton_cost_marker_id() -> None:
    """One rollup per record: a fact with nothing to key on is the family alone."""
    assert ids.evidence_id("basicly-vkh0.12", family="cost", content=None) == cost_marker_id(
        "basicly-vkh0.12"
    )


def test_recording_the_same_fact_twice_yields_the_same_id() -> None:
    """Idempotence is the whole reason an evidence id is derived rather than minted."""
    first = ids.evidence_id("basicly-ab12", "found-info", "the ledger holds 636 records")
    second = ids.evidence_id("basicly-ab12", "found-info", "the ledger holds 636 records")
    assert first == second
    assert ids.is_evidence_id(first)


@pytest.mark.parametrize(
    "other",
    [
        {"kind": "decision"},
        {"content": "a different fact"},
        {"generation": 2},
        {"family": "run"},
        {"record_id": "basicly-ab12.1"},
    ],
)
def test_a_different_fact_gets_a_different_evidence_id(other: dict) -> None:
    """Every component of the key discriminates; none is decoration."""
    base = {
        "record_id": "basicly-ab12",
        "kind": "found-info",
        "content": "a fact",
        "family": "",
        "generation": 1,
    }
    assert ids.evidence_id(**base) != ids.evidence_id(**{**base, **other})


def test_no_caller_text_ever_reaches_an_evidence_id() -> None:
    """Content is hashed, never embedded — which is what keeps a slug out of an id."""
    evidence = ids.evidence_id(
        "basicly-ab12", "found-info", "a title with-hyphens, spaces and 'quotes'"
    )
    assert ids.is_evidence_id(evidence)
    for fragment in ("with-hyphens", "spaces", "quotes", "title"):
        assert fragment not in evidence
    assert ids.record_id_of(evidence) == "basicly-ab12"


def test_an_evidence_id_is_rejected_where_a_record_id_is_required() -> None:
    """The two kinds are not interchangeable, and the validators say so."""
    evidence = ids.evidence_id("basicly-ab12", "found-info", "a fact")
    assert not ids.is_record_id(evidence)
    assert not ids.is_evidence_id("basicly-ab12")
    with pytest.raises(ids.IdError):
        ids.record_id_of("basicly-ab12")


@pytest.mark.parametrize("bad", ["my-repo", "Basicly", "9lives", "", "basicly_core"])
def test_minting_refuses_a_prefix_that_could_not_appear_in_an_id(bad: str) -> None:
    """The hyphen case is the shipped defect; the rest are the same charset rule."""
    with pytest.raises(ids.IdError):
        ids.mint_root_id(bad, frozenset())


@pytest.mark.parametrize("bad", [{"family": "my-run"}, {"generation": 0}])
def test_the_evidence_id_refuses_what_would_break_its_own_shape(bad: dict) -> None:
    """A hyphenated family and a zeroth generation are both unrepresentable."""
    with pytest.raises(ids.IdError):
        ids.evidence_id("basicly-ab12", "kind", "content", **bad)


def test_a_singleton_evidence_id_needs_a_family() -> None:
    """With no content and no family there is nothing to name the fact."""
    with pytest.raises(ids.IdError):
        ids.evidence_id("basicly-ab12", content=None)


def test_an_evidence_id_needs_a_fact_to_derive_from() -> None:
    """Neither kind nor content would digest ``":"`` — one id shared by every record."""
    with pytest.raises(ids.IdError):
        ids.evidence_id("basicly-ab12")
    with pytest.raises(ids.IdError):
        ids.evidence_id("basicly-ab12", family="run")
    # A kind alone is enough: a fact can have a sort and no payload.
    assert ids.is_evidence_id(ids.evidence_id("basicly-ab12", "reopened"))


# --- AC 3, the mechanism: the commit-message gate, tripped ----------------------


def test_a_hyphenated_id_really_is_refused_by_the_gate() -> None:
    """The positive control for every gate assertion below (basicly-jms0).

    The gate derives its prefixes by splitting a known id at the *first* hyphen, so
    ``basicly-my-slug`` is read as the id ``basicly-my`` — which is in no ledger. This
    is why the minter forbids a hyphen rather than merely preferring not to.
    """
    slug_id = "basicly-my-slug"
    is_valid, error = gate.validate(f"fix(tracker): work ({slug_id})", {slug_id})
    assert not is_valid
    assert "unknown issue id" in error
    assert "basicly-my" in error
    assert not ids.is_record_id(slug_id)


def test_a_minted_record_id_passes_the_commit_message_gate() -> None:
    """The ids this module mints are the ids the gate accepts."""
    record = ids.mint_root_id("basicly", frozenset())
    is_valid, error = gate.validate(f"feat(tracker): mint ids ({record})", {record})
    assert is_valid, error
    assert gate._candidate_ids(f"feat(tracker): mint ids ({record})", {record}) == {record}


def test_a_minted_child_id_passes_the_commit_message_gate() -> None:
    """Including a nested one, which is the shape the loop's children actually use."""
    root = ids.mint_root_id("basicly", frozenset())
    child = ids.next_child_id(root, {root})
    grandchild = ids.next_child_id(child, {root, child})
    for record in (child, grandchild):
        message = f"fix(tracker): child work ({record})"
        is_valid, error = gate.validate(message, {record})
        assert is_valid, error
        assert gate._candidate_ids(message, {record}) == {record}


def test_an_evidence_id_in_a_message_resolves_to_its_record() -> None:
    """Quoting evidence in a commit message references the record, never a bogus id."""
    root = ids.mint_root_id("basicly", frozenset())
    child = ids.next_child_id(root, {root})
    evidence = ids.evidence_id(child, "build", "deadbeef", family="run", generation=2)
    message = f"chore(tracker): record the dispatch {evidence} ({child})"
    assert gate._candidate_ids(message, {child}) == {child}
    is_valid, error = gate.validate(message, {child})
    assert is_valid, error


def test_every_minted_id_at_every_declared_length_passes_the_gate() -> None:
    """The 4 → 5 → 6 → 7 growth cannot smuggle in a shape the gate rejects."""
    rng = random.Random(31)
    for length, population in DECLARED_THRESHOLDS:
        record = ids.mint_root_id("basicly", _filler(population - 1), rng=rng)
        assert len(record.split("-", 1)[1]) == length
        is_valid, error = gate.validate(f"feat(tracker): mint ({record})", {record})
        assert is_valid, error
