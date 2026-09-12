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

GOOD = {
    "flowchart": "flowchart TD\n  a[Start] --> b[End]\n",
    "sequence": "sequenceDiagram\n  participant P\n  P->>P: tick\n",
    "state": "stateDiagram-v2\n  [*] --> A\n  A --> [*]\n",
    "class with a note": 'classDiagram\n  class A\n  note for A "x"\n',
    "mindmap": "mindmap\n  root\n    leaf\n",
}
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
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate: Any = _load(SCRIPT, "check_mermaid")


def _document(root: Path, name: str, diagrams: dict[str, str]) -> Path:
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
    document = _document(tmp_path, "d.md", GOOD)
    assert gate.main([str(document)]) == 0
    printed = capsys.readouterr().out
    assert f"{len(GOOD)} block(s) in 1 document(s)" in printed
    assert f"mermaid {gate.HOSTING_VERSION}" in printed
    assert gate.HOSTING_SURFACE in printed


def test_a_refused_block_names_the_file_the_line_the_version_and_the_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = _document(tmp_path, "d.md", {**GOOD, "broken": BAD_ARROW})
    assert gate.main([str(document)]) == 1
    printed = capsys.readouterr().out
    fences = [
        number
        for number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1)
        if line.strip() == "```mermaid"
    ]
    assert f"{document.name}:{fences[-1]}:" in printed
    assert f"mermaid {gate.HOSTING_VERSION} refused this block" in printed
    assert "Parse error on line 2" in printed
    assert "^" in printed


def test_render_refuses_three_blocks_the_parser_accepts(tmp_path: Path) -> None:
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
    document = tmp_path / "d.md"
    document.write_text("# Doc\n\n```python\nprint(1)\n```\n", encoding="utf-8")
    assert gate.main([str(document)]) == 2
    assert "no mermaid block found in 1 document(s)" in capsys.readouterr().err


def test_a_missing_renderer_script_fails_rather_than_skipping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "RENDERER_SCRIPT", ".scripts/not_a_renderer.mjs")
    assert gate.main([str(_document(tmp_path, "d.md", GOOD))]) == 2
    assert "is missing" in capsys.readouterr().err


def test_an_unavailable_node_fails_rather_than_skipping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(gate, "NODE", "not-a-node-on-any-path")
    assert gate.main([str(_document(tmp_path, "d.md", GOOD))]) == 2
    assert "could not run not-a-node-on-any-path" in capsys.readouterr().err


def test_a_renderer_that_writes_nothing_usable_fails_rather_than_skipping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:

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
    monkeypatch.setattr(gate, "HOSTING_VERSION", "0.0.0")
    assert gate.main([str(_document(tmp_path, "d.md", GOOD))]) == 2
    assert "no longer matches the hosting one" in capsys.readouterr().err


def test_a_mermaid_fence_quoted_inside_a_longer_fence_is_not_a_diagram(
    tmp_path: Path,
) -> None:
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

    assert gate.main([]) == 0
    printed = capsys.readouterr().out
    assert " 0 block(s) " not in printed
    assert "all render" in printed
