"""Tests for the mermaid render gate (basicly-yy82zy, backlog B3).

The gate's value is that it fails, so most of these assert a failure and name what it has
to report. Four ways it could fail open, each pinned here:

* **A block the renderer refuses must not read as a pass.** That is the whole defect class:
  a red error box on the hosting site that every other gate here is blind to.
* **`parse` must not be mistaken for `render`.** One case runs the same three blocks through
  both instruments and shows the parser accepting every one. Without it, "render is the
  stronger instrument" is a claim in a docstring.
* **A missing renderer must fail, not skip.** A skipped check and a passing check are the
  same line in a log, and the skip is the one that ships the red box.
* **An empty population must fail.** Zero blocks is the collector breaking, not the tree
  being clean, and this repository's diagrams are the reason the gate exists.

Node is slow to start, so the cases that need it are batched: five ``node`` runs, not one
per assertion.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_mermaid.py"

# Blocks mermaid 11.16.1 draws. Each is a different diagram family, because the shims in
# `render_mermaid.mjs` stand in for browser text measurement and a family that measures
# differently is where a shim stops being good enough.
GOOD = {
    "flowchart": "flowchart TD\n  a[Start] --> b[End]\n",
    "sequence": "sequenceDiagram\n  participant P\n  P->>P: tick\n",
    "state": "stateDiagram-v2\n  [*] --> A\n  A --> [*]\n",
    "class with a note": 'classDiagram\n  class A\n  note for A "x"\n',
    "mindmap": "mindmap\n  root\n    leaf\n",
}
# Blocks the parser accepts and the renderer refuses, measured against 11.16.1. Each fails
# inside the diagram's own `draw`, which `mermaid.parse` never reaches.
DRAW_ONLY = {
    "a subgraph id repeating a node id": "flowchart TD\n  subgraph a\n    a-->b\n  end\n",
    "a gantt task with no parseable date": (
        "gantt\n  dateFormat YYYY-MM-DD\n  section S\n  t :a1, notadate, 3d\n"
    ),
    "a state note on a state that does not exist": (
        "stateDiagram-v2\n  A --> B\n  note right of Zzz : hi\n"
    ),
}
BAD_ARROW = "flowchart TD\n  a[Start] -->< b[End]\n"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# A path-loaded module is `ModuleType` to a type checker, so everything reached through
# `gate` is already `Any`.
gate: Any = _load(SCRIPT, "check_mermaid")


def _document(root: Path, name: str, diagrams: dict[str, str]) -> Path:
    """A markdown file carrying *diagrams*, each in its own fenced mermaid block."""
    body = "# Doc\n\n" + "".join(
        f"## {title}\n\n```mermaid\n{text}```\n\n" for title, text in diagrams.items()
    )
    document = root / name
    document.write_text(body, encoding="utf-8")
    return document


def _blocks(root: Path, diagrams: dict[str, str], name: str = "d.md") -> tuple[Any, ...]:
    return gate.collect(root, (_document(root, name, diagrams),))


def test_every_good_block_renders_and_the_report_names_the_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A green run states the count, the renderer version and the surface it was read from."""
    document = _document(tmp_path, "d.md", GOOD)
    assert gate.main([str(document)]) == 0
    printed = capsys.readouterr().out
    assert f"{len(GOOD)} block(s) in 1 document(s)" in printed
    assert f"mermaid {gate.HOSTING_VERSION}" in printed
    assert gate.HOSTING_SURFACE in printed


def test_a_refused_block_names_the_file_the_line_the_version_and_the_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The four things a reader needs to find the diagram and see why it was refused."""
    document = _document(tmp_path, "d.md", {**GOOD, "broken": BAD_ARROW})
    assert gate.main([str(document)]) == 1
    printed = capsys.readouterr().out
    # Derived from the fixture rather than counted by hand: the broken block is last, so
    # the reported line has to be the last opening fence in the document.
    fences = [
        number
        for number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1)
        if line.strip() == "```mermaid"
    ]
    assert f"{document.name}:{fences[-1]}:" in printed
    assert f"mermaid {gate.HOSTING_VERSION} refused this block" in printed
    assert "Parse error on line 2" in printed
    # The caret line, which is the part a one-line report drops.
    assert "^" in printed


def test_render_refuses_three_blocks_the_parser_accepts(tmp_path: Path) -> None:
    """The criterion, as a two-directional control: `parse` takes all three, `render` none."""
    blocks = _blocks(tmp_path, DRAW_ONLY)
    assert len(blocks) == len(DRAW_ONLY)
    _, accepted = gate.render(REPO_ROOT, blocks, "parse")
    version, refused = gate.render(REPO_ROOT, blocks)
    assert version == gate.HOSTING_VERSION
    assert accepted == {}, "the parser was expected to accept all three"
    assert sorted(refused) == sorted(range(len(DRAW_ONLY)))


def test_a_document_with_no_mermaid_block_fails_rather_than_passing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty population is the collector failing, and a green run over it is the fail-open."""
    document = tmp_path / "d.md"
    document.write_text("# Doc\n\n```python\nprint(1)\n```\n", encoding="utf-8")
    assert gate.main([str(document)]) == 2
    assert "no mermaid block found in 1 document(s)" in capsys.readouterr().err


def test_a_missing_renderer_script_fails_rather_than_skipping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skip and a pass are the same line in a log, and the skip ships the red box."""
    monkeypatch.setattr(gate, "RENDERER_SCRIPT", ".scripts/not_a_renderer.mjs")
    assert gate.main([str(_document(tmp_path, "d.md", GOOD))]) == 2
    assert "is missing" in capsys.readouterr().err


def test_an_unavailable_node_fails_rather_than_skipping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same rule one layer down: no node on PATH is a failure, not an excuse.

    The interpreter name is redirected rather than `subprocess.run` replaced, so the real
    `OSError` from the real call is what the recovery path receives.
    """
    monkeypatch.setattr(gate, "NODE", "not-a-node-on-any-path")
    assert gate.main([str(_document(tmp_path, "d.md", GOOD))]) == 2
    assert "could not run not-a-node-on-any-path" in capsys.readouterr().err


def test_a_renderer_that_writes_nothing_usable_fails_rather_than_skipping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A renderer exiting zero with no report is the quietest way for this gate to lie.

    `subprocess.run` is replaced here, unlike the case above, because no command that
    exits zero with empty output exists under the same name on all three platforms.
    """

    class Silent:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(gate.subprocess, "run", lambda *_a, **_k: Silent())
    assert gate.main([str(_document(tmp_path, "d.md", GOOD))]) == 2
    assert "no usable report" in capsys.readouterr().err


def test_a_renderer_version_drifting_from_the_hosting_one_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pin that no longer matches the hosting surface is a gate agreeing with itself."""
    monkeypatch.setattr(gate, "HOSTING_VERSION", "0.0.0")
    assert gate.main([str(_document(tmp_path, "d.md", GOOD))]) == 2
    assert "no longer matches the hosting one" in capsys.readouterr().err


def test_a_mermaid_fence_quoted_inside_a_longer_fence_is_not_a_diagram(
    tmp_path: Path,
) -> None:
    """Prose showing a broken diagram is not a broken diagram; only the real block is drawn."""
    document = tmp_path / "d.md"
    document.write_text(
        "# Doc\n\n````\n```mermaid\nnotADiagram\n```\n````\n\n```mermaid\n"
        + GOOD["flowchart"]
        + "```\n",
        encoding="utf-8",
    )
    blocks = gate.collect(tmp_path, (document,))
    assert [block.text for block in blocks] == [GOOD["flowchart"].rstrip("\n")]


def test_an_indented_mermaid_fence_is_collected_at_its_own_line(tmp_path: Path) -> None:
    """A block nested under a list item is still a block, reported at its own opening fence."""
    document = tmp_path / "d.md"
    document.write_text(
        "# Doc\n\n- item\n\n  ```mermaid\n  " + GOOD["flowchart"].strip() + "\n  ```\n",
        encoding="utf-8",
    )
    blocks = gate.collect(tmp_path, (document,))
    assert [(block.line, block.doc) for block in blocks] == [(5, "d.md")]


def test_the_committed_tree_holds_blocks_and_every_one_renders(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The gate against the real tree, which is the population it was built for.

    Its own positive control: a green run over zero blocks would satisfy every other
    test in this file, so the count is asserted to be non-zero here.
    """
    assert gate.main([]) == 0
    printed = capsys.readouterr().out
    assert " 0 block(s) " not in printed
    assert "all render" in printed
