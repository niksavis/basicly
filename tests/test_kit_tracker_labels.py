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
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


labels = _load(KIT_DIR / "labels.py", "tracker_labels")
migrate = _load(KIT_DIR / "migrate.py", "tracker_migrate_for_labels")


def test_the_writer_vocabulary_is_exactly_what_the_engine_writes() -> None:

    assert {owned_write.OWNED_PROVENANCE, mirror.MIRROR_PROVENANCE} == labels.WRITER_LABELS


def test_the_two_vocabularies_really_do_share_one_key() -> None:
    assert labels.KEY_LABEL == migrate.PROVENANCE_KEY


def test_a_writer_vocabulary_value_gates_rather_than_routing_a_decision() -> None:

    for label in sorted(labels.WRITER_LABELS):
        assert labels.disposition(label) == labels.DISPOSITION_GATE
        assert labels.strength_of(label) == labels.strength_of(labels.EXTRACTED)


def test_the_vocabulary_still_gates_on_an_exact_string_only() -> None:

    for near in ("engine ", "Engine", "dual write", "dual-writer", "engine-x"):
        assert labels.disposition(near) == labels.DISPOSITION_DECIDE
        assert labels.strength_of(near) == 0


@pytest.mark.parametrize("dialect", sorted(labels.DIALECT_KEYS))
def test_every_dialect_names_a_complete_pair_of_structural_keys(dialect: str) -> None:
    target_key, type_key = labels.DIALECT_KEYS[dialect]
    assert target_key and type_key
    assert dialect == f"{target_key}/{type_key}"
