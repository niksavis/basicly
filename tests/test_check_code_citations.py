"""Tests for the code-to-document citation ratchet (basicly-e2mz.49).

The gate's value is that it fails, so most of these assert a failure and name what it has to
report. Three ways it could fail open, each pinned here:

* **An unattributable mark must not read as a pass.** A bare mark naming no document is the
  213-strong population this gate exists for; if it were skipped rather than counted, the
  gate would report a clean tree over the whole defect.
* **A binding must not be able to appear quietly.** One line can make a directory's marks
  resolve against a document nobody chose, so ``binding_count`` is checked in both
  directions and a binding that stopped matching anything is reported.
* **A recorded count falls as well as rises.** An unbanked fall licenses regrowth for free,
  the shape `check_module_size.py` was built to refuse.

Every failing case carries its **positive control** — the same input, corrected, asserted to
resolve. A rule that only ever reports is indistinguishable from one that matches everything.

The section sign is assembled from :data:`MARK` and never written literally, in this file and
in the gate, so neither cites anything and neither counts itself.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_code_citations.py"
RATCHET = REPO_ROOT / ".scripts" / "ratchet.py"
FRAGMENT = REPO_ROOT / "basicly.d" / "basicly-e2mz.49.toml"

MARK = "\N{SECTION SIGN}"
DOC = "docs/requirements/spec.md"
SPEC = "# Spec\n\n## 4. Ordering\n\n### 4.6 The aggregate\n\n## Unnumbered\n"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# A path-loaded module is `ModuleType` to a type checker, so everything reached through
# `gate` is already `Any` — hence `Any` on the helpers below.
gate = _load(SCRIPT, "check_code_citations")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A tree holding one numbered document, which the citing lines below point at."""
    document = tmp_path / DOC
    document.parent.mkdir(parents=True)
    document.write_text(SPEC, encoding="utf-8")
    return tmp_path


def _cite(text: str, *, path: str = "src/basicly/mod.py", bindings: Any = None) -> list[Any]:
    return gate.cited(path, text, bindings or {})


def _reasons(repo: Path, text: str, **kwargs: Any) -> list[str]:
    return sorted(gate.unresolved(repo, _cite(text, **kwargs)).values())


def _ratchet(frozen: dict[str, int] | None = None, bindings: int = 0) -> Any:
    return gate.Ratchet(frozen=frozen or {}, count=bindings)


def _citation(path: str = "src/basicly/mod.py", line: int = 1) -> Any:
    return gate.Citation(path=path, line=line, section="4", document=None)


# --- attributing a mark to a document -------------------------------------------------


def test_a_document_named_on_the_line_attributes_every_mark_on_it() -> None:
    """Two marks written after one path are two citations of it, not one and an orphan."""
    found = _cite(f"# See `{DOC}` {MARK}4 and {MARK}4.6 for the fold.\n")
    assert [(item.section, item.document) for item in found] == [("4", DOC), ("4.6", DOC)]
    assert [item.site for item in found] == ["src/basicly/mod.py:1"] * 2


def test_a_name_after_the_mark_does_not_attribute_it() -> None:
    """Otherwise a document mentioned three lines later would claim an earlier mark."""
    assert _cite(f"# {MARK}4 is stated in `{DOC}`.\n")[0].document is None


def test_a_bare_mark_carries_no_document_without_a_binding() -> None:
    """The 213-mark population: counted and reported, never skipped."""
    assert _cite(f"# The fold is {MARK}4.1.\n")[0].document is None


def test_a_prefix_binding_attributes_a_bare_mark_and_the_longest_prefix_wins() -> None:
    """One reviewable line is what turned the kit's 113 bare marks into checked ones."""
    bindings = {"src/": "docs/a.md", "src/basicly/": "docs/b.md"}
    assert _cite(f"# The fold is {MARK}4.1.\n", bindings=bindings)[0].document == "docs/b.md"
    assert gate.bound_document("tests/t.py", bindings) is None


def test_a_name_on_the_line_beats_the_binding() -> None:
    """A module that cites a second document explicitly means the one it named."""
    found = _cite(f"# `{DOC}` {MARK}4.\n", bindings={"src/": "docs/other.md"})
    assert found[0].document == DOC


# --- resolving the target --------------------------------------------------------------


def test_only_a_numbered_heading_is_a_citable_target(repo: Path) -> None:
    """The number is what a citation may rely on, so an unnumbered heading is not one."""
    assert gate.headings(repo, DOC) == frozenset({"4", "4.6"})
    assert gate.headings(repo, "docs/absent.md") is None


@pytest.mark.parametrize(
    ("named", "expected"),
    [
        (DOC, DOC),
        ("spec.md", DOC),
        ("absent.md", None),
    ],
)
def test_a_document_resolves_by_path_or_by_unambiguous_basename(
    repo: Path, named: str, expected: str | None
) -> None:
    """A basename matching two documents resolves to neither rather than to a guess."""
    assert gate.resolve_document(repo, named) == expected
    (repo / "docs" / "spec.md").write_text(SPEC, encoding="utf-8")
    assert gate.resolve_document(repo, "spec.md") is None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (f"# The fold is {MARK}4.1.\n", f"{MARK}4.1 names no document"),
        (f"# `docs/gone.md` {MARK}4.\n", "`docs/gone.md` is not a document in this tree"),
        (f"# `{DOC}` {MARK}9.\n", f"`{DOC}` defines no {MARK}9"),
    ],
)
def test_each_way_a_mark_fails_to_reach_a_heading_is_named(
    repo: Path, text: str, reason: str
) -> None:
    """Three reasons, three repairs — and the corrected form of each resolves."""
    assert _reasons(repo, text) == [reason]
    assert _reasons(repo, f"# `{DOC}` {MARK}4.6.\n") == []


def test_an_unnumbered_heading_is_the_defect_this_gate_was_built_for(repo: Path) -> None:
    """Renumbering a cited section is what leaves a mark resolving to the wrong place."""
    text = f"# `{DOC}` {MARK}4.6 states the rule.\n"
    assert _reasons(repo, text) == []
    (repo / DOC).write_text(SPEC.replace("### 4.6 The aggregate", "### The aggregate"))
    assert _reasons(repo, text) == [f"`{DOC}` defines no {MARK}4.6"]


# --- the ratchet ----------------------------------------------------------------------


def _collect(
    repo: Path,
    reasons: dict[Any, str],
    ratchet: Any,
    bindings: Any = None,
    citations: list[Any] | None = None,
) -> list[Any]:
    return gate.collect(repo, citations or list(reasons), reasons, bindings or {}, ratchet)


def test_a_module_absent_from_the_table_may_not_carry_one_unresolved_mark(repo: Path) -> None:
    """The list is closed, which is what makes the recorded debt a debt and not a licence."""
    findings = _collect(repo, {_citation(): "a reason"}, _ratchet())
    assert [finding.subject for finding in findings] == ["src/basicly/mod.py"]
    assert "no recorded debt" in findings[0].detail
    assert "line 1: a reason" in findings[0].detail
    assert _collect(repo, {}, _ratchet()) == []


def test_a_recorded_count_may_only_fall(repo: Path) -> None:
    """Growth fails naming both numbers; the frozen count itself passes."""
    reasons = {_citation(line=number): "a reason" for number in (1, 2)}
    frozen = {"src/basicly/mod.py": 1}
    assert "up from the frozen 1" in _collect(repo, reasons, _ratchet(frozen))[0].detail
    assert _collect(repo, dict(list(reasons.items())[:1]), _ratchet(frozen)) == []


def test_a_count_that_fell_is_banked_and_a_module_at_zero_loses_its_entry(repo: Path) -> None:
    """The frozen set is walked in union with the observed one, so an absent module is seen."""
    frozen = {"src/basicly/mod.py": 2}
    banked = _collect(repo, {_citation(): "a reason"}, _ratchet(frozen))
    assert 'set `"src/basicly/mod.py" = 1`' in banked[0].remedy
    graduated = _collect(repo, {}, _ratchet(frozen))
    assert [finding.subject for finding in graduated] == ["src/basicly/mod.py"]
    assert 'delete `"src/basicly/mod.py"`' in graduated[0].remedy


def test_a_binding_naming_no_document_in_the_tree_is_refused(repo: Path) -> None:
    """A binding that resolves to nothing would un-attribute a whole directory in silence."""
    citations = [_citation()]
    findings = _collect(repo, {}, _ratchet(bindings=1), {"src/": "docs/gone.md"}, citations)
    assert "is not a document in this tree" in findings[0].detail
    assert _collect(repo, {}, _ratchet(bindings=1), {"src/": DOC}, citations) == []


def test_a_binding_whose_prefix_matches_nothing_is_reported(repo: Path) -> None:
    """Nothing is attributed to it, so nothing fails — the shape that reads as satisfied."""
    citations = [_citation()]
    stale = gate.collect(repo, citations, {}, {"vendor/": DOC}, _ratchet(bindings=1))
    assert [finding.subject for finding in stale] == ["vendor/"]
    assert "delete" in stale[0].remedy
    assert gate.collect(repo, citations, {}, {"src/": DOC}, _ratchet(bindings=1)) == []


def test_a_binding_cannot_be_added_or_removed_without_saying_so(repo: Path) -> None:
    """One line can make a directory resolve against a document nobody chose."""
    added = _collect(repo, {}, _ratchet(bindings=0), {"src/": DOC})
    assert "1 binding(s) declared but binding_count is 0" in added[0].detail
    assert "count_delta = +1" in added[0].remedy
    removed = _collect(repo, {}, _ratchet(bindings=1))
    assert "count_delta = -1" in removed[0].remedy


# --- the recorded state, and the wiring ------------------------------------------------


def test_the_recorded_state_is_read_from_pyproject_and_refuses_a_missing_table(
    tmp_path: Path,
) -> None:
    """A gate defaulting to a permissive record passes everything, which is worse than off."""
    assert gate.load_ratchet(REPO_ROOT).count == len(gate.load_bindings(REPO_ROOT))
    (tmp_path / "pyproject.toml").write_text("[tool.other]\n", encoding="utf-8")
    with pytest.raises(gate.RatchetError, match="bindings"):
        gate.load_bindings(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.code_citations.bindings]\nsrc = 3\n", encoding="utf-8"
    )
    with pytest.raises(gate.RatchetError, match="one document"):
        gate.load_bindings(tmp_path)


def test_no_frozen_entry_sits_at_zero() -> None:
    """An entry at zero licenses regrowth back to nothing, which is a contradiction."""
    table = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    frozen = table["tool"]["code_citations"]["frozen"]
    assert frozen and all(count > 0 for count in frozen.values())


def test_the_gate_passes_on_this_repository() -> None:
    """The recorded state describes this tree — run as a consumer runs it."""
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False, cwd=REPO_ROOT
    )
    assert completed.returncode == 0, completed.stderr
    assert "resolved to a heading" in completed.stdout


def test_the_gate_fails_end_to_end_on_a_heading_that_went_away(tmp_path: Path) -> None:
    """The acceptance criterion, run the way a commit runs it, in both directions.

    A scratch repository rather than a mutation of this one: the gate resolves its root from
    its own location, so copying it into a tmp tree exercises `git ls-files`, the TOML read
    and the heading scan together without putting a deliberate defect in the working tree.
    It is a three-file unit — it reads through `ratchet.py`, which imports
    :mod:`basicly.dropin` off the ``src`` it derives from its own path — so all three travel.
    """
    scripts = tmp_path / ".scripts"
    scripts.mkdir()
    copied = shutil.copy(SCRIPT, scripts / SCRIPT.name)
    shutil.copy(RATCHET, scripts / RATCHET.name)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.code_citations]\nbinding_count = 0\n\n[tool.code_citations.bindings]\n\n"
        "[tool.code_citations.frozen]\n",
        encoding="utf-8",
    )
    document = tmp_path / DOC
    document.parent.mkdir(parents=True)
    document.write_text(SPEC, encoding="utf-8")
    package = tmp_path / "src" / "basicly"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(REPO_ROOT / "src" / "basicly" / "dropin.py", package / "dropin.py")
    (package / "mod.py").write_text(f'"""`{DOC}` {MARK}4.6 states the rule."""\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, copied], capture_output=True, text=True, check=False, cwd=tmp_path
        )

    # Positive control on the fixture, not on the gate: a run that finds no citing module
    # reports nothing and exits 0, so the green below would mean nothing and the red after it
    # would name the wrong cause. It fired once: `mod.py` written without an encoding became
    # cp1252 on windows, and the gate's utf-8 read turned the mark into U+FFFD (basicly-t31pvf).
    baseline = run()
    assert baseline.returncode == 0
    assert "in 0 module(s)" not in baseline.stdout, (
        f"the fixture holds no readable citation, so this asserts nothing: {baseline.stdout}"
    )
    document.write_text(SPEC.replace("### 4.6 The aggregate", "### The aggregate"))
    completed = run()

    assert completed.returncode == 1
    # Exactly one failure, which is what the acceptance criterion asks for: one citing module,
    # one finding, and the site inside it.
    reported = [line for line in completed.stderr.splitlines() if "unresolved citation" in line]
    assert len(reported) == 1
    assert "src/basicly/mod.py: 1 unresolved citation(s)" in completed.stderr
    assert f"line 1: `{DOC}` defines no {MARK}4.6" in completed.stderr


def test_neither_the_gate_nor_this_test_cites_anything() -> None:
    """Both spell the mark throughout; a self-counting gate would freeze its own text."""
    for path in (SCRIPT, Path(__file__)):
        assert gate.cited(path.name, path.read_text(encoding="utf-8"), {}) == []


def test_the_gate_is_wired_as_a_verify_check() -> None:
    """An instrument nothing runs is the defect class this repo keeps paying for."""
    wired = tomllib.loads(FRAGMENT.read_text(encoding="utf-8"))["verify"]["checks"]
    assert [check["name"] for check in wired] == ["code-citations"]
    assert SCRIPT.name in " ".join(wired[0]["command"])
    assert wired[0]["modes"] == ["fast", "full"]
