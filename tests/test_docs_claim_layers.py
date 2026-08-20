"""Tests for the derived architecture layering block (basicly-h7bknm).

The defect was a number nothing read. Section 34 stated the tier and band counts of the
layering contract, and no script, test or gate compared them with `.importlinter` - so on
2026-08-20 the document said 36 tiers where the contract had 37 and its band labels summed to
98 modules where the contract had 102, with the whole suite green. A corrected number is wrong
again on the next tier, so the tests that matter here are the ones a *corrected* document
would pass and a *derived* one fails: a tier added to the contract has to turn the gate red.

The band boundaries are the declared half, and every way that declaration can go stale under a
contract edit gets its own test. Each of them raises rather than rendering, on purpose: a band
count nobody can derive, printed as if it were derived, is the defect this block replaced.

Every mutation runs against ``work_repo`` through ``--root``, so nothing here writes to the
checkout it is testing.
"""

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
    """Load the docs-claims script module from its path (it is not a package)."""
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
    """Invoke the gate against *root*, this block only."""
    return claims.main([mode, "--root", str(root), "--block", BLOCK])


def _edit_contract(root: Path, old: str, new: str) -> None:
    """Rewrite one literal in the copied contract, refusing a fixture that has drifted."""
    path = root / CONTRACT
    text = path.read_text(encoding="utf-8")
    assert old in text, f"the fixture no longer matches the contract it mutates: {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _band_counts(root: Path) -> list[int]:
    """The module count each band label in the rendered block states, in band order."""
    body = block_body((root / ARCHITECTURE_MD).read_text(encoding="utf-8"), BLOCK)
    return [
        int(match.group(1)) for line in body if (match := re.search(r"\N{EM DASH} (\d+)", line))
    ]


def test_the_stated_tier_and_module_counts_are_the_contracts_own() -> None:
    """The claim the document makes, against the contract, by a path the block does not use.

    The block's own reader is not evidence for the block: the counts below come from a plain
    line count over the ``layers =`` continuation lines, which is what a reader with `grep`
    would do and what the defect report did to find the disagreement in the first place.
    """
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
    """The two counts the block's first sentence states, as numbers."""
    found = re.match(r"The (\d+) tiers hold (\d+) modules", sentence)
    assert found is not None, sentence
    return int(found[1]), int(found[2])


def test_a_tier_added_to_the_contract_makes_the_gate_refuse_until_the_block_is_regenerated(
    work_repo: Path,
) -> None:
    """The second acceptance criterion, and the whole reason a correction was not enough.

    The tier is planted *inside* the bottom band rather than under it, so what moves is a
    count and nothing else - which is the case a hand-corrected document passes and this
    block has to fail.
    """
    assert _run(work_repo, "--check") == 0
    before = block_body((work_repo / ARCHITECTURE_MD).read_text(encoding="utf-8"), BLOCK)
    tiers, modules = _tiers_and_modules(before[0])

    _edit_contract(work_repo, "\n    tracker_paths\n", "\n    tracker_paths\n    planted_tier\n")
    assert _run(work_repo, "--check") == 1
    assert _run(work_repo, "--fix") == 0
    assert _run(work_repo, "--check") == 0

    body = block_body((work_repo / ARCHITECTURE_MD).read_text(encoding="utf-8"), BLOCK)
    # The delta, never the absolute: a tier planted inside the bottom band moves each count
    # by exactly one. Asserting `39`/`105` pinned this test to the tree it was written on, and
    # the next lane to add a module broke it - which is the defect the whole block exists to
    # refuse, arriving in the test that proves the block works (basicly-jcl4rm).
    assert _tiers_and_modules(body[0]) == (tiers + 1, modules + 1)
    assert sum(_band_counts(work_repo)) == modules + 1


def test_the_block_says_the_band_boundaries_are_declared_rather_than_derived() -> None:
    """A number the block cannot derive may not read as one it did.

    Nothing in the tree declares where a band starts, so the grouping is an editorial claim
    over the contract and the block has to say so where a reader meets the counts. Naming the
    module that declares it is what makes the claim followable.
    """
    body = " ".join(block_body((REPO / ARCHITECTURE_MD).read_text(encoding="utf-8"), BLOCK))
    assert "derived from `.importlinter`" in body
    assert "The band *boundaries* are not" in body
    assert "`.scripts/docs_claim_layers.py`" in body


def test_the_declared_exemptions_are_read_from_the_contract_not_from_the_diagram(
    work_repo: Path,
) -> None:
    """The parse's own positive control, and it caught a real fail-open on the way in.

    The contract's prose names ``unmatched_ignore_imports_alerting``, so keying the parse on
    the bare word landed inside that comment and returned no exemptions at all - which
    renders a diagram with no exemption edges drawn and nothing to disagree with it. So the
    live tree is asserted to yield both pairs before any mutation, and removing one is
    asserted to take its edge out of the document.
    """
    assert layers.exemptions(REPO) == [("loop", "supervise"), ("policy", "decisions")]
    _edit_contract(work_repo, "\n    basicly.policy -> basicly.decisions\n", "\n")
    assert layers.exemptions(work_repo) == [("loop", "supervise")]
    assert _run(work_repo, "--fix") == 0
    body = " ".join(block_body((work_repo / ARCHITECTURE_MD).read_text(encoding="utf-8"), BLOCK))
    assert "loop imports supervise" in body
    assert "policy imports decisions" not in body


def test_a_band_boundary_the_contract_no_longer_declares_is_refused(work_repo: Path) -> None:
    """A boundary module renamed out from under the declaration cannot render a count."""
    _edit_contract(work_repo, "\n    capability_proof\n", "\n    proof_of_capability\n")
    with pytest.raises(layers.ClaimError, match="capability_proof"):
        layers.grouped(layers.tiers(work_repo))
    assert _run(work_repo, "--check") == 1
    assert _run(work_repo, "--fix") == 1


def test_a_tier_below_the_last_band_belongs_to_no_band_and_is_named(work_repo: Path) -> None:
    """Bands partition the stack: a tier appended under the bottom one is not absorbed."""
    _edit_contract(work_repo, "\n    stemmer\n", "\n    stemmer\n    planted_leaf\n")
    with pytest.raises(layers.ClaimError, match="planted_leaf"):
        layers.grouped(layers.tiers(work_repo))


def test_a_band_example_the_contract_moved_out_of_its_band_is_refused(work_repo: Path) -> None:
    """The example modules are a claim about placement, so they are held to the contract."""
    _edit_contract(work_repo, "\n    board_snapshot\n", "\n    board_snapshot | mirror\n")
    _edit_contract(work_repo, "comment_rows | mirror", "comment_rows")
    with pytest.raises(layers.ClaimError, match="mirror"):
        layers.grouped(layers.tiers(work_repo))


def test_an_absent_contract_is_reported_as_unevaluable_rather_than_as_a_clean_run(
    tmp_path: Path,
) -> None:
    """A missing source is not a claim that holds - the distinction `ClaimError` exists for."""
    with pytest.raises(layers.ClaimError):
        layers.tiers(tmp_path)
    (tmp_path / CONTRACT).write_text("[importlinter]\nroot_package = basicly\n", encoding="utf-8")
    with pytest.raises(layers.ClaimError, match="engine-layering"):
        layers.tiers(tmp_path)


def test_the_block_is_registered_with_the_docs_claims_gate() -> None:
    """An instrument nothing runs is the defect class this repo keeps paying for."""
    registered = {block.name: block.path for block in claims.BLOCKS}
    assert registered[BLOCK] == ARCHITECTURE_MD
