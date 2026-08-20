"""Tests for the provenance vocabulary and the payload names it declares (basicly-493g5f).

The sibling `test_kit_tracker_provenance` had 9 tokens of size headroom, which is the reason
these are here rather than there; the seam is real either way - this module owns *what a label
means*, that one owns writing, reading and folding an edge.

**The pins against the engine's own constants are the point.** The kit cannot import
`basicly`, so a string it must agree with is a string it can silently drift from. Every such
agreement is asserted here from the side that can see both.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from basicly import mirror, owned_write

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _load(path: Path, name: str) -> Any:
    """Load a standalone kit module by path, the way a consumer without basicly would."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


labels = _load(KIT_DIR / "labels.py", "tracker_labels")
migrate = _load(KIT_DIR / "migrate.py", "tracker_migrate_for_labels")


def test_the_writer_vocabulary_is_exactly_what_the_engine_writes() -> None:
    """The collision, pinned from the side that can see both vocabularies.

    A rename in `owned_write` or `mirror` silently reopens the blindness this closed: the
    kit cannot import `basicly`, so nothing else can catch it.
    """
    assert {owned_write.OWNED_PROVENANCE, mirror.MIRROR_PROVENANCE} == labels.WRITER_LABELS


def test_the_two_vocabularies_really_do_share_one_key() -> None:
    """The equality is the defect. Asserted so a later split of the key fails here first."""
    assert labels.KEY_LABEL == migrate.PROVENANCE_KEY


def test_a_writer_vocabulary_value_gates_rather_than_routing_a_decision() -> None:
    """An event the engine's seam appended is one a command asked for - `EXTRACTED`'s claim.

    Measured before this landed: 142 edge events carried one of these, folding to 133 edges
    disposed `decide` for want of a vocabulary rather than for want of a fact, which is why
    `gating_edges` read 932 of 1065 (basicly-493g5f).
    """
    for label in sorted(labels.WRITER_LABELS):
        assert labels.disposition(label) == labels.DISPOSITION_GATE
        assert labels.strength_of(label) == labels.strength_of(labels.EXTRACTED)


def test_the_vocabulary_still_gates_on_an_exact_string_only() -> None:
    """Two exact strings were added, not a prefix and not a fallback.

    A label this version does not know still routes a decision, and a *near* miss of a
    writer value is one of those - which is what keeps the widening from being a fail-open.
    """
    for near in ("engine ", "Engine", "dual write", "dual-writer", "engine-x"):
        assert labels.disposition(near) == labels.DISPOSITION_DECIDE
        assert labels.strength_of(near) == 0


@pytest.mark.parametrize("dialect", sorted(labels.DIALECT_KEYS))
def test_every_dialect_names_a_complete_pair_of_structural_keys(dialect: str) -> None:
    """One table, read by two folds. A dialect naming one key would read half a population."""
    target_key, type_key = labels.DIALECT_KEYS[dialect]
    assert target_key and type_key
    assert dialect == f"{target_key}/{type_key}"
