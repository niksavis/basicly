from __future__ import annotations

import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[1] / ".basicly" / "core" / "kit" / "comments" / "cli.py"


def _run(command: str, path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, "-I", str(CLI), command, str(path)],
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize("suffix", ["jsx", "tsx"])
@pytest.mark.parametrize(
    "content",
    [
        "see http://x",
        "Don't remove this",
        '/* literal text */ and "quoted text"',
        "https://one.test\n// more text",
        "a > b && c // text",
    ],
)
def test_jsx_text_is_not_reported_or_changed(suffix: str, content: str, tmp_path: Path) -> None:
    path = tmp_path / f"view.{suffix}"
    source = f"const view = <Text>{content}</Text>;\n"
    path.write_text(source, encoding="utf-8")

    checked = _run("check", path)
    fixed = _run("fix", path)

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.parametrize(
    "source",
    [
        "function view() { return <Text>Don't http://x</Text>; }\n",
        "async function view() { return await <Text>Don't http://x</Text>; }\n",
        "throw <Text>Don't http://x</Text>;\n",
        "const value = typeof <Text>Don't http://x</Text>;\n",
        "const value = <Ä>Don't http://x</Ä>;\n",
    ],
)
def test_jsx_expression_contexts_preserve_their_text(source: str, tmp_path: Path) -> None:
    path = tmp_path / "view.tsx"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.parametrize("suffix", ["jsx", "tsx"])
def test_comments_in_jsx_expressions_are_removed_without_changing_children(
    suffix: str, tmp_path: Path
) -> None:
    path = tmp_path / f"view.{suffix}"
    source = """const view = (
  <Box href="http://x" value={{label: "don't // change"}}>
    Don't remove http://x or /* this text */.
    <Text>{items.map(item => <Text>{item /* item prose */}</Text>)}</Text>
    <>{/* fragment prose */}<Input value={/https?:\\/\\//.test(url)} /></>
    {flag ? <Text>http://yes</Text> : <Text>don't</Text>}
  </Box>
); // trailing prose
"""
    path.write_text(source, encoding="utf-8")

    checked = _run("check", path)
    fixed = _run("fix", path)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "3 prose comments" in checked.stdout
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    expected = source.replace("/* item prose */", "").replace("/* fragment prose */", "")
    expected = expected.replace("// trailing prose", "")
    assert path.read_text(encoding="utf-8") == expected
    assert _run("check", path).returncode == 0
    assert _run("fix", path).returncode == 0
    assert path.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize(
    ("suffix", "expression"),
    [
        ("tsx", "left < right && right > 0"),
        ("tsx", "<T,>(value: T) => value"),
        ("ts", "<Thing>input"),
        ("mts", "<Thing>input"),
        ("tsx", "factory<Thing>(input)"),
    ],
)
def test_non_jsx_angle_brackets_keep_their_code(
    suffix: str, expression: str, tmp_path: Path
) -> None:
    path = tmp_path / f"code.{suffix}"
    source = f"const value = {expression}; // actual prose\n"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source.replace("// actual prose", "")


@pytest.mark.parametrize("suffix", ["jsx", "tsx"])
@pytest.mark.parametrize(
    "expression",
    [
        "<Text>http://x",
        "<Box><Text>http://x</Box>",
        "<Text>{value /* prose */</Text>",
        '<Text title="never closed>text</Text>',
        "<Text>{/never closed}</Text>",
    ],
)
def test_uncertain_jsx_is_refused_without_writing(
    suffix: str, expression: str, tmp_path: Path
) -> None:
    path = tmp_path / f"broken.{suffix}"
    source = f"const view = {expression};\n"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 2, fixed.stdout + fixed.stderr
    assert "refused" in fixed.stdout
    assert path.read_text(encoding="utf-8") == source
