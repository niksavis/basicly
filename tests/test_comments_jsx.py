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
        "const value = 1 / <Text>see http://x</Text>;\n",
        "const value = 1 < <Text>see http://x</Text>;\n",
        "const value = !!<Text>see http://x</Text>;\n",
    ],
)
def test_jsx_expression_contexts_preserve_their_text(source: str, tmp_path: Path) -> None:
    path = tmp_path / "view.tsx"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.parametrize(
    "operator",
    [
        "/",
        "*",
        "**",
        "%",
        "+",
        "-",
        "<<",
        ">>",
        ">>>",
        "<",
        "<=",
        ">",
        ">=",
        "==",
        "===",
        "!=",
        "!==",
        "&",
        "|",
        "^",
        "&&",
        "||",
        "??",
        "in",
        "instanceof",
    ],
)
def test_jsx_binary_operands_preserve_text(operator: str, tmp_path: Path) -> None:
    path = tmp_path / "view.tsx"
    source = f"const view = value {operator} <Text>http://x</Text>; // actual prose\n"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source.replace("// actual prose", "")


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
        ("tsx", "left <<T> 0"),
        ("tsx", "left++ <T> 0"),
        ("tsx", "left! <T> 0"),
        ("tsx", "/value/ <T> 0"),
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


@pytest.mark.parametrize(
    "type_arguments",
    [
        "string",
        "Array<string>",
        "Map<string, Array<number>>",
        "{label: string}",
        '"x>y"',
        "() => string",
        "<T>(value: T) => T",
    ],
)
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("content", ["see http://x", "Don't remove http://x"])
def test_generic_jsx_elements_preserve_text(
    type_arguments: str, nested: bool, content: str, tmp_path: Path
) -> None:
    path = tmp_path / "view.tsx"
    element = f"<Select<{type_arguments}>>{content}</Select>"
    source = f"const view = {('<Box>' + element + '</Box>') if nested else element};\n"
    path.write_text(source, encoding="utf-8")

    checked = _run("check", path)
    fixed = _run("fix", path)

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.parametrize(
    "source",
    [
        "const choose = <T extends string>(value: T) => value; // actual prose\n",
        "const choose = <T /* type prose */ extends string>(value: T) => value;\n",
        "const choose = <T extends Array<string>>(value: T) => value; // actual prose\n",
        "const choose = <T = string>(value: T) => value; // actual prose\n",
        "const choose = <const T extends string>(value: T) => value; // actual prose\n",
        "const choose = <T extends {map: <U>(value: U) => U}>(value: T) => value; "
        "// actual prose\n",
    ],
)
def test_generic_arrows_remain_code(source: str, tmp_path: Path) -> None:
    path = tmp_path / "view.tsx"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source.replace("// actual prose", "").replace(
        "/* type prose */", ""
    )


@pytest.mark.parametrize(
    "header",
    [
        "async <T extends {map: <U>(value: U) => U}>",
        "async <const T extends {map: <U>(value: U) => U}>",
        "async <T, U extends {map: <V>(value: V) => V}>",
        "async <T = {map: <U>(value: U) => U}>",
        "async <T, U = {map: <V>(value: V) => V}>",
        "async<T extends {map: <U>(value: U) => U}>",
        "async /* actual prose */ <T extends {map: <U>(value: U) => U}>",
        "async <T, U /* type prose */ extends {map: <V>(value: V) => V}>",
    ],
)
def test_async_generic_headers_remain_code(header: str, tmp_path: Path) -> None:
    path = tmp_path / "view.tsx"
    source = f"const f = {header}(value: T) => value; // actual prose\n"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source.replace("// actual prose", "").replace(
        "/* actual prose */", ""
    ).replace("/* type prose */", "")


@pytest.mark.parametrize(
    "continuation",
    [
        "(value: T): Promise<T> => value",
        r"(value: T = /\)/ as unknown as T) => value",
        "(value: T = <Text>http://x</Text> as unknown as T) => value",
        "(value: T = (() => {return /* atom prose */ <Text>http://x</Text>;})() "
        "as unknown as T) => value",
    ],
)
def test_async_generic_lookahead_keeps_parameter_atoms(continuation: str, tmp_path: Path) -> None:
    path = tmp_path / "view.tsx"
    source = (
        "const f = async <T, U extends {map: <V>(value: V) => V}>"
        f"{continuation}; // actual prose\n"
    )
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source.replace("// actual prose", "").replace(
        "/* atom prose */", ""
    )


@pytest.mark.parametrize(
    "expression",
    [
        "(async <T, <Text>http://x</Text>)",
        "(async <T, <Text>(value) => http://x</Text>)",
        "(compare <T, <Text>http://x</Text>)",
        "(async <T, U = () => <Text>http://x</Text>)",
        "(async <T, U > (value) ? <Text>http://x</Text> : null)",
        "async<T, U>(value)",
        "async<T>0",
    ],
)
def test_async_identifiers_do_not_create_generic_arrows(expression: str, tmp_path: Path) -> None:
    path = tmp_path / "view.tsx"
    source = f"const view = {expression}; // actual prose\n"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source.replace("// actual prose", "")


@pytest.mark.parametrize(
    "element",
    [
        "<Text extends>Don't remove http://x</Text>",
        "<Text extends={value}>Don't remove http://x</Text>",
        "<Text>() => Don't remove http://x</Text>",
    ],
)
def test_generic_arrow_disambiguation_keeps_actual_jsx(element: str, tmp_path: Path) -> None:
    path = tmp_path / "view.tsx"
    source = f"const view = {element};\n"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source


def test_javascript_jsx_boolean_attributes_are_not_type_constraints(tmp_path: Path) -> None:
    path = tmp_path / "view.jsx"
    source = "const view = <Text extends label>http://x</Text>;\n"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source


def test_generic_jsx_type_comments_are_removed_without_changing_children(tmp_path: Path) -> None:
    path = tmp_path / "view.tsx"
    source = (
        "const view = <Box><Select<Array< /* type prose */ string>>>"
        "http://x</Select><Select<string> value={value} /></Box>;\n"
    )
    path.write_text(source, encoding="utf-8")

    checked = _run("check", path)
    fixed = _run("fix", path)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "1 prose comments" in checked.stdout
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source.replace("/* type prose */", "")


def test_comments_between_jsx_attributes_remain_code(tmp_path: Path) -> None:
    path = tmp_path / "view.tsx"
    source = (
        'const view = <Select<string> /* attribute prose */ title="http://label">'
        "http://x</Select>;\n"
    )
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source.replace("/* attribute prose */", "")


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
