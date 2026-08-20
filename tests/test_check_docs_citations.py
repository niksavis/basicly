"""Tests for the docs-citations gate (basicly-miqr).

Driven against a fixture tree in `tmp_path`, never against this repo's own documents: a gate
asserted on live prose becomes a report on whatever the docs say today, and any lane editing
a paragraph turns the suite red. The two real-tree runs assert only what a hardcoded
``REPO_ROOT`` can be held to — that the entry point runs and identifies itself, and that it
is wired as a check.

Every stale case carries its own **positive control** in the same test: the corrected form of
the same citation, asserted to pass. A gate that only ever reports is indistinguishable from
one whose rule never matches, and this rule's whole population failed on the day it landed —
so "it found something" was not evidence that it could find nothing.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_docs_citations.py"

MODULE = '''\
"""A fixture module."""

MARKER = 1


def anchored() -> int:
    """Two lines in, so a citation can land inside it or beside it."""
    total = MARKER
    return total
'''
# `anchored` spans lines 6-9 and `MARKER` sits on line 3; line 5 is blank.
INSIDE, BESIDE, BLANK, PAST_EOF = 7, 3, 5, 99


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# A path-loaded module is `ModuleType` to a type checker, so `gate.Finding` is not usable in
# an annotation and everything reached through `gate` is already `Any` — hence `Any` below.
gate = _load(SCRIPT, "check_docs_citations")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A tree with one cited module and a `docs/` directory to write claims into."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "widget.py").write_text(MODULE, encoding="utf-8")
    (tmp_path / "docs").mkdir()
    return tmp_path


def _claim(repo: Path, text: str) -> tuple[int, int, tuple[Any, ...], tuple[Any, ...]]:
    """Write *text* as the only document and scan it, as (seen, checkable, stale, unverifiable)."""
    doc = repo / "docs" / "claims.md"
    doc.write_text(text, encoding="utf-8")
    return gate.scan(repo, (doc,))


def test_a_planted_false_claim_about_a_symbol_is_named_with_the_line_it_moved_to(
    repo: Path,
) -> None:
    """The finding a planner needs: which claim, and where the symbol actually is."""
    _, checkable, found, _ = _claim(repo, f"The engine calls `anchored` (`widget.py:{BESIDE}`).\n")
    assert checkable == 1
    assert len(found) == 1
    assert found[0].citation == f"widget.py:{BESIDE}"
    assert "`anchored` is at :6" in found[0].detail

    _, checkable, found, _ = _claim(repo, f"The engine calls `anchored` (`widget.py:{INSIDE}`).\n")
    assert (checkable, found) == (1, ())


def test_a_backtick_boundary_between_the_path_and_the_line_number_is_still_a_citation(
    repo: Path,
) -> None:
    """basicly-v5c8ob: `` `widget.py`:3 `` was seen by nothing, which reads as no citation.

    Not the lookbehind, as the record guessed: the lookbehind passes against an opening
    backtick. It is the **closing** one, which sits between the path and the colon whenever
    an author ticks the path and leaves the line number outside. The pattern found nothing,
    so the citation was not counted, not checked and not reported — the one outcome a
    presence-based gate cannot tell apart from a document that carries no citations.
    """
    outside = f"The engine calls `anchored` (`widget.py`:{BESIDE}).\n"
    seen, checkable, found, _ = _claim(repo, outside)
    assert (seen, checkable) == (1, 1)
    assert [finding.citation for finding in found] == [f"widget.py:{BESIDE}"]

    inside = f"The engine calls `anchored` (`widget.py:{BESIDE}`).\n"
    assert _claim(repo, inside)[:2] == (seen, checkable)
    assert not _claim(repo, outside.replace(f":{BESIDE}", f":{INSIDE}"))[2]


def test_the_backtick_boundary_admits_no_prose(repo: Path) -> None:
    """The loosened pattern's negative control, with its own positive control beside it.

    A gate widened to see more must be shown not to have started seeing prose. The document
    below legitimately writes a colon and a number four ways — a clock time, a ratio, a
    count after a backticked command, and a bare section-and-line reference — and carries
    one real citation. Asserting only the zero would be ambiguous between "no prose matched"
    and "the scan is broken"; the real citation is what rules the second out.
    """
    prose = (
        "At 12:30 the ratio was 3:1 and `uv run pytest -q`: 4 failures.\n"
        "A `widget.py` reference with no number, then a bare `:99` continuation.\n"
        f"The `anchored` body (`widget.py`:{INSIDE}) is the only citation here.\n"
    )
    seen, checkable, found, blind = _claim(repo, prose)
    assert (seen, checkable) == (1, 1)
    assert (found, blind) == ((), ())


def test_a_citation_onto_a_blank_line_or_past_end_of_file_is_named(repo: Path) -> None:
    """No reading of the prose can make either right, so neither needs a named symbol."""
    for number in (BLANK, PAST_EOF):
        _, _, found, _ = _claim(repo, f"Recorded at `widget.py:{number}`.\n")
        assert [finding.detail for finding in found] == ["past end-of-file or a blank line"]
    _, _, found, _ = _claim(repo, f"Recorded at `widget.py:{BESIDE}`.\n")
    assert found == ()


def test_a_sentence_naming_no_symbol_of_the_cited_file_is_reported_unverifiable(
    repo: Path,
) -> None:
    """The fail-open half of basicly-v5c8ob: counted, unchecked, and it used to exit zero.

    The claim planted here is false — line 3 is `MARKER`, not "something happens" — and no
    reading of the sentence can be held to the symbol rule, because it names no symbol. So
    the citation has to leave the scan as a finding of its own kind; the positive control is
    the same sentence naming `MARKER`, which becomes checkable and stays silent.
    """
    seen, checkable, found, blind = _claim(repo, f"Something happens at `widget.py:{BESIDE}`.\n")
    assert (seen, checkable, found) == (1, 0, ())
    assert [finding.detail for finding in blind] == [gate._UNVERIFIABLE]
    assert blind[0].citation == f"widget.py:{BESIDE}"

    seen, checkable, found, blind = _claim(repo, f"The `MARKER` at `widget.py:{BESIDE}`.\n")
    assert (seen, checkable, found, blind) == (1, 1, (), ())


def test_only_a_module_level_symbol_anchors_a_citation(repo: Path) -> None:
    """A local named `total` matches half this repo's prose; admitting one makes the rule fuzz."""
    _, checkable, *_ = _claim(repo, f"The `total` it accumulates (`widget.py:{BESIDE}`).\n")
    assert checkable == 0
    _, checkable, *_ = _claim(repo, f"The `MARKER` it reads (`widget.py:{BESIDE}`).\n")
    assert checkable == 1


def test_the_modules_own_stem_does_not_anchor_a_citation(repo: Path) -> None:
    """`widget.py:3` beside the word `widget` names the file twice, not a symbol in it."""
    (repo / "src" / "widget.py").write_text(f"{MODULE}\n\ndef widget() -> None:\n    pass\n")
    _, checkable, *_ = _claim(repo, f"See `widget` (`widget.py:{BESIDE}`).\n")
    assert checkable == 0


def test_a_bare_identifier_inside_a_fenced_block_anchors_a_citation(repo: Path) -> None:
    """A fence carries no backticks, and it is where this repo tabulates its dispatch sites."""
    fenced = f"```text\nwidget.py:{BESIDE}   anchored\n```\n"
    _, checkable, found, _ = _claim(repo, fenced)
    assert checkable == 1
    assert len(found) == 1
    assert not _claim(repo, fenced.replace(f":{BESIDE}", ":6"))[2]


def test_an_unresolvable_or_ambiguous_citation_is_named_rather_than_guessed(repo: Path) -> None:
    """Two files with the cited basename cannot be told apart, so neither is assumed."""
    _, _, found, _ = _claim(repo, "Recorded at `absent.py:1`.\n")
    assert [finding.detail for finding in found] == ["no such file, or two files match"]
    (repo / "docs" / "widget.py").write_text(MODULE, encoding="utf-8")
    _, _, found, _ = _claim(repo, f"The `anchored` at `widget.py:{INSIDE}`.\n")
    assert [finding.detail for finding in found] == ["no such file, or two files match"]


def test_the_gate_exits_non_zero_only_while_a_claim_is_stale(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit code is the gate, and the corrected document clears it — the AC, end to end."""
    monkeypatch.setattr(gate, "REPO_ROOT", repo)
    (repo / "docs" / "claims.md").write_text(
        f"The engine calls `anchored` (`widget.py:{BESIDE}`).\n", encoding="utf-8"
    )
    assert gate.main(["--strict"]) == 1
    (repo / "docs" / "claims.md").write_text(
        f"The engine calls `anchored` (`widget.py:{INSIDE}`).\n", encoding="utf-8"
    )
    assert gate.main(["--strict"]) == 0


def _finding(doc: str) -> Any:
    return gate.Finding(doc, 1, "widget.py:3", "outside the symbol named here")


def test_a_document_absent_from_the_baseline_may_not_carry_one_stale_citation() -> None:
    """The list is closed, which is what makes the recorded debt a debt and not a licence."""
    recorded = (_finding("a.md"),) * 2
    verdict = gate.verdicts((*recorded, _finding("b.md")), {"a.md": 2})
    assert verdict == ["b.md: 1 stale citation(s); this document has no recorded debt"]


def test_a_recorded_document_may_only_fall() -> None:
    """Growth fails, and so does an unbanked fall — the shape module-size already refuses."""
    assert "up from the frozen 1" in gate.verdicts((_finding("a.md"),) * 2, {"a.md": 1})[0]
    assert gate.verdicts((_finding("a.md"),), {"a.md": 1}) == []
    fell = gate.verdicts((), {"a.md": 1})
    assert 'set "a.md" = 0' in fell[0]


def test_an_unverifiable_citation_is_ratcheted_by_its_own_table() -> None:
    """The 32 that existed are debt; a document not in the table may carry none.

    Same three verdicts as the stale table and the same closed list, because the failure mode
    is the same: leaving the higher number licenses regrowth back to it for free.
    """
    blind = (_finding("a.md"),) * 2
    verdict = gate.verdicts(
        (*blind, _finding("b.md")), {"a.md": 2}, "unverifiable", gate.UNVERIFIABLE_TABLE
    )
    assert verdict == ["b.md: 1 unverifiable citation(s); this document has no recorded debt"]
    grew = gate.verdicts(blind, {"a.md": 1}, "unverifiable", gate.UNVERIFIABLE_TABLE)
    assert "up from the frozen 1" in grew[0]
    assert gate.UNVERIFIABLE_TABLE in grew[0]
    fell = gate.verdicts((), {"a.md": 1}, "unverifiable", gate.UNVERIFIABLE_TABLE)
    assert 'set "a.md" = 0' in fell[0]


def test_the_gate_exits_non_zero_on_an_unverifiable_citation_alone(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole of basicly-v5c8ob's second half, at the exit code.

    Nothing here is stale: line 3 is live code inside a real module. It is simply a claim
    the gate cannot read, and it used to exit zero. The positive control is the same
    citation with `MARKER` named, which is verifiable and passes.
    """
    monkeypatch.setattr(gate, "REPO_ROOT", repo)
    doc = repo / "docs" / "claims.md"
    doc.write_text(f"Something happens at `widget.py:{BESIDE}`.\n", encoding="utf-8")
    assert gate.main(["--strict"]) == 1
    doc.write_text(f"The `MARKER` at `widget.py:{BESIDE}`.\n", encoding="utf-8")
    assert gate.main(["--strict"]) == 0


def test_the_baseline_is_read_from_pyproject_and_refuses_a_missing_table(tmp_path: Path) -> None:
    """A gate that defaults to a permissive baseline passes everything, which is worse.

    The real table is asserted to *load*, never to hold a particular document. It named
    one until 2026-08-16, when the last debt was banked to zero and the table emptied —
    so the assertion failed on the tree reaching the state the ratchet exists to produce.
    A test that only passes while some document is still in debt is a test against
    success.
    """
    assert gate.load_frozen(REPO_ROOT) is not None
    assert gate.load_frozen(REPO_ROOT, "unverifiable") is not None
    (tmp_path / "pyproject.toml").write_text(
        '[tool.docs_citations.frozen]\n"a.md" = 3\n', encoding="utf-8"
    )
    assert gate.load_frozen(tmp_path) == {"a.md": 3}
    with pytest.raises(gate.RatchetError, match="unverifiable"):
        gate.load_frozen(tmp_path, "unverifiable")
    (tmp_path / "pyproject.toml").write_text("[tool.other]\n", encoding="utf-8")
    with pytest.raises(gate.RatchetError, match="docs_citations"):
        gate.load_frozen(tmp_path)


def test_the_gate_runs_over_this_repo() -> None:
    """The entry point works against the hardcoded repo root, whatever it finds there."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode in (0, 1), result.stderr
    assert "[docs-citations]" in result.stdout


def test_the_gate_is_wired_as_a_verify_check() -> None:
    """An instrument nothing runs is the defect class this repo keeps paying for."""
    fragment = tomllib.loads(
        (REPO_ROOT / "basicly.d" / "basicly-miqr.toml").read_text(encoding="utf-8")
    )
    wired = fragment["verify"]["checks"]
    assert [check["name"] for check in wired] == ["docs-citations"]
    assert SCRIPT.name in " ".join(wired[0]["command"])
    assert wired[0]["modes"] == ["fast", "full"]
