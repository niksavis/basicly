from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from tests.doc_blocks import block_body

REPO = Path(__file__).resolve().parents[1]
ARCHITECTURE_MD = "docs/architecture/architecture.md"
CONTRACT = ".importlinter"
BLOCK = "layering-contract"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "docs_claims", REPO / ".scripts" / "docs_claims.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


claims = _load_module()
layers = claims.layers


def _run(root: Path, mode: str) -> int:
    return claims.main([mode, "--root", str(root), "--block", BLOCK])


def _edit_contract(root: Path, old: str, new: str) -> None:
    path = root / CONTRACT
    text = path.read_text(encoding="utf-8")
    assert old in text, f"the fixture no longer matches the contract it mutates: {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _band_counts(root: Path) -> list[int]:
    body = block_body((root / ARCHITECTURE_MD).read_text(encoding="utf-8"), BLOCK)
    return [
        int(match.group(1)) for line in body if (match := re.search(r"\N{EM DASH} (\d+)", line))
    ]


def test_the_stated_tier_and_module_counts_are_the_contracts_own() -> None:

    text = (REPO / CONTRACT).read_text(encoding="utf-8")
    body = text.split("[importlinter:contract:engine-layering]", 1)[1]
    tier_lines = [
        line
        for line in body.split("layers =", 1)[1].splitlines()[1:]
        if line.startswith("    ") and not line.strip().startswith("#")
    ]
    tier_lines = tier_lines[
        : next((index for index, line in enumerate(tier_lines) if "->" in line), len(tier_lines))
    ]
    modules = sum(len(line.split("|")) for line in tier_lines)

    stated = block_body((REPO / ARCHITECTURE_MD).read_text(encoding="utf-8"), BLOCK)[0]
    assert stated.startswith(f"The {len(tier_lines)} tiers hold {modules} modules")
    assert sum(_band_counts(REPO)) == modules


def _tiers_and_modules(sentence: str) -> tuple[int, int]:
    found = re.match(r"The (\d+) tiers hold (\d+) modules", sentence)
    assert found is not None, sentence
    return int(found[1]), int(found[2])


def test_a_tier_added_to_the_contract_makes_the_gate_refuse_until_the_block_is_regenerated(
    work_repo: Path,
) -> None:

    assert _run(work_repo, "--check") == 0
    before = block_body((work_repo / ARCHITECTURE_MD).read_text(encoding="utf-8"), BLOCK)
    tiers, modules = _tiers_and_modules(before[0])

    _edit_contract(work_repo, "\n    tracker_paths\n", "\n    tracker_paths\n    planted_tier\n")
    assert _run(work_repo, "--check") == 1
    assert _run(work_repo, "--fix") == 0
    assert _run(work_repo, "--check") == 0

    body = block_body((work_repo / ARCHITECTURE_MD).read_text(encoding="utf-8"), BLOCK)
    assert _tiers_and_modules(body[0]) == (tiers + 1, modules + 1)
    assert sum(_band_counts(work_repo)) == modules + 1


def test_the_block_says_the_band_boundaries_are_declared_rather_than_derived() -> None:

    body = " ".join(block_body((REPO / ARCHITECTURE_MD).read_text(encoding="utf-8"), BLOCK))
    assert "derived from `.importlinter`" in body
    assert "The band *boundaries* are not" in body
    assert "`.scripts/docs_claim_layers.py`" in body


def test_the_declared_exemptions_are_read_from_the_contract_not_from_the_diagram(
    work_repo: Path,
) -> None:

    assert layers.exemptions(REPO) == [("policy", "decisions")]
    _edit_contract(work_repo, "\n    basicly.policy -> basicly.decisions\n", "\n")
    assert layers.exemptions(work_repo) == []
    assert _run(work_repo, "--fix") == 0
    body = " ".join(block_body((work_repo / ARCHITECTURE_MD).read_text(encoding="utf-8"), BLOCK))
    assert "policy imports decisions" not in body


def test_a_band_boundary_the_contract_no_longer_declares_is_refused(work_repo: Path) -> None:
    _edit_contract(work_repo, "\n    capability_proof\n", "\n    proof_of_capability\n")
    with pytest.raises(layers.ClaimError, match="capability_proof"):
        layers.grouped(layers.tiers(work_repo))
    assert _run(work_repo, "--check") == 1
    assert _run(work_repo, "--fix") == 1


def test_a_tier_below_the_last_band_belongs_to_no_band_and_is_named(work_repo: Path) -> None:
    _edit_contract(work_repo, "\n    stemmer\n", "\n    stemmer\n    planted_leaf\n")
    with pytest.raises(layers.ClaimError, match="planted_leaf"):
        layers.grouped(layers.tiers(work_repo))


def test_a_band_example_the_contract_moved_out_of_its_band_is_refused(work_repo: Path) -> None:
    _edit_contract(work_repo, "\n    board_snapshot\n", "\n    board_snapshot | mirror\n")
    _edit_contract(work_repo, "comment_rows | mirror", "comment_rows")
    with pytest.raises(layers.ClaimError, match="mirror"):
        layers.grouped(layers.tiers(work_repo))


def test_an_absent_contract_is_reported_as_unevaluable_rather_than_as_a_clean_run(
    tmp_path: Path,
) -> None:
    with pytest.raises(layers.ClaimError):
        layers.tiers(tmp_path)
    (tmp_path / CONTRACT).write_text("[importlinter]\nroot_package = basicly\n", encoding="utf-8")
    with pytest.raises(layers.ClaimError, match="engine-layering"):
        layers.tiers(tmp_path)


def test_the_block_is_registered_with_the_docs_claims_gate() -> None:
    registered = {block.name: block.path for block in claims.BLOCKS}
    assert registered[BLOCK] == ARCHITECTURE_MD
