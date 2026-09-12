"""Tests for the comments kit (basicly-phglc2x).

Every fixture carries a deliberate trap: a comment opener living inside a string, a
regular expression, a template literal, a here-document or a shell parameter expansion.
Those are the shapes that make a naive strip corrupt source, so they are the assertions
that matter - a test that only proves prose is removed proves the easy half.

The Python half is driven against this repository's own tree in
`test_the_whole_python_tree_strips_and_proves`, because 560 real files are a stronger
statement about the lexer than any fixture.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1] / ".basicly" / "core" / "kit" / "comments"


def _load(file_name: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, KIT / file_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


strip = _load("strip.py", "basicly_comments_kit_strip")
scan = sys.modules["basicly_comments_kit_scan"]
directives = sys.modules["basicly_comments_kit_directives"]
languages = sys.modules["basicly_comments_kit_languages"]

JS = """\
// a prose comment
const url = "https://example.com/not-a-comment";  // trailing prose
const re = /https:\\/\\/x/;  /* block prose */
const tpl = `a ${1 + 1} // not a comment`;
/* eslint-disable no-console */
const div = 10 / 2 / 1;
"""

CSS = """\
/* prose about the palette */
.a { color: red; }
.b { content: "/* not a comment */"; }
/*! license banner */
"""

HTML = """\
<!-- prose about the layout -->
<div class="x">text <!-- inline prose --></div>
<!--[if IE]><p>ie</p><![endif]-->
"""

SHELL = """\
#!/usr/bin/env bash
# prose about the script
echo "a # not a comment"
x=${y#prefix}
cat <<'EOF'
# this hash is data, not a comment
EOF
# shellcheck disable=SC2086
echo $x
"""

CSHARP = """\
// prose about the class
public class A {
    // prose about the method
    public string S() { return "// not a comment"; }
    /* block prose */
}
"""

SQL = """\
-- prose about the query
SELECT 'a -- not a comment' AS x
FROM t; /* block prose */
"""

POWERSHELL = """\
# prose about the script
<# block prose #>
Write-Host "a # not a comment"
"""

CASES = [
    (
        "a.js",
        JS,
        ["a prose comment", "trailing prose", "block prose"],
        ["not-a-comment", "eslint-disable", "// not a comment", "10 / 2 / 1", "https:"],
    ),
    ("b.css", CSS, ["prose about the palette"], ["/* not a comment */", "license banner"]),
    ("c.html", HTML, ["prose about the layout", "inline prose"], ["[if IE]", "<p>ie</p>"]),
    (
        "d.sh",
        SHELL,
        ["prose about the script"],
        [
            "#!/usr/bin/env bash",
            "a # not a comment",
            "${y#prefix}",
            "# this hash is data, not a comment",
            "shellcheck disable=SC2086",
        ],
    ),
    (
        "e.cs",
        CSHARP,
        ["prose about the class", "prose about the method", "block prose"],
        ['"// not a comment"'],
    ),
    ("f.sql", SQL, ["prose about the query", "block prose"], ["'a -- not a comment'"]),
    ("g.ps1", POWERSHELL, ["prose about the script", "block prose"], ['"a # not a comment"']),
]


@pytest.mark.parametrize(("name", "source", "gone", "kept"), CASES)
def test_the_strip_removes_prose_and_keeps_everything_else(
    name: str, source: str, gone: list[str], kept: list[str], tmp_path: Path
) -> None:
    """Each language: the prose goes, and every trap and directive survives."""
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")

    stripped = strip.strip_source(path, source)

    for text in gone:
        assert text not in stripped, f"{name}: {text!r} should have been removed"
    for text in kept:
        assert text in stripped, f"{name}: {text!r} should have survived"


@pytest.mark.parametrize(("name", "source"), [(case[0], case[1]) for case in CASES])
def test_the_strip_is_idempotent(name: str, source: str, tmp_path: Path) -> None:
    """A second pass changes nothing, which is the invariant a mis-lex would break."""
    path = tmp_path / name
    once = strip.strip_source(path, source)
    assert strip.strip_source(path, once) == once


def test_a_docstring_that_is_its_owners_only_statement_becomes_pass(tmp_path: Path) -> None:
    """Removing it outright would leave a body that does not parse."""
    path = tmp_path / "x.py"
    source = 'class E(Exception):\n    """Prose."""\n'

    stripped = strip.strip_source(path, source)

    assert "Prose" not in stripped
    assert "pass" in stripped
    ast.parse(stripped)


def test_a_non_ascii_docstring_does_not_eat_the_next_line(tmp_path: Path) -> None:
    """`ast` reports byte columns and `tokenize` reports characters; an em dash is three.

    Before this was handled the strip took the newline and one space of the next line's
    indentation with it, and two real files in this repository stopped parsing.
    """
    path = tmp_path / "x.py"
    source = 'def f():\n    """One em dash — here."""\n    x = 1\n    return x\n'

    stripped = strip.strip_source(path, source)

    assert "    x = 1" in stripped
    ast.parse(stripped)


def test_an_unterminated_string_is_refused_rather_than_stripped(tmp_path: Path) -> None:
    """A file the lexer cannot finish is a file it must not edit."""
    path = tmp_path / "x.js"
    with pytest.raises(scan.LexError):
        strip.strip_source(path, 'const a = "never closed;\n// prose\n')


def test_a_language_the_kit_does_not_claim_is_refused(tmp_path: Path) -> None:
    """Config and prose keep their comments, so the kit declines rather than guessing."""
    path = tmp_path / "x.yaml"
    assert not scan.is_covered(path)
    with pytest.raises(ValueError, match="claims no language"):
        strip.strip_source(path, "# a yaml comment\nkey: value\n")


@pytest.mark.parametrize(
    "text",
    [
        "# noqa: E402",
        "# nosec B603",
        "# type: ignore[arg-type]",
        "# pragma: no cover",
        "# comment-density-waiver: cohesion: x",
        "#!/usr/bin/env python3",
        "// eslint-disable-next-line",
        "// @ts-expect-error",
        "/*! (c) someone */",
        "# shellcheck disable=SC2086",
        "// SPDX-License-Identifier: MIT",
    ],
)
def test_a_directive_is_never_prose(text: str) -> None:
    """A comment some tool reads must survive, or the strip breaks the build."""
    assert directives.is_directive(text)


@pytest.mark.parametrize(
    "text",
    [
        "# what this function does",
        "# TODO: fix this later",
        "// the caller owns the lock",
        "/* the palette comes from the brand guide */",
        "# global state is a mistake",
    ],
)
def test_ordinary_prose_is_not_read_as_a_directive(text: str) -> None:
    """The negative control: an over-broad allowlist would leave prose in place silently."""
    assert not directives.is_directive(text)


def test_the_whole_python_tree_strips_and_proves() -> None:
    """Every tracked Python file strips clean, and each one proves its own edit.

    This is the test that found the byte-versus-character defect. It writes nothing.
    """
    repo = Path(__file__).resolve().parents[1]
    listed = subprocess.run(  # nosec B603 B607
        ["git", "ls-files", "*.py"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert len(listed) > 500, "the positive control: the tree should hold hundreds of modules"

    refused = []
    for name in listed:
        path = repo / name
        try:
            strip.strip_source(path, path.read_text(encoding="utf-8"))
        except (strip.UnsafeStripError, scan.LexError) as err:
            refused.append(f"{name}: {err}")

    assert refused == []


def test_the_covered_suffix_list_names_every_language_in_the_table() -> None:
    """A language in the table that the CLI cannot name is a language nobody can use."""
    covered = set(languages.covered_suffixes())
    for language in languages.LANGUAGES:
        assert set(language.extensions) <= covered, language.name
    assert set(languages.PYTHON_EXTENSIONS) <= covered


cli = _load("cli.py", "basicly_comments_kit_cli")


def test_check_reports_and_exits_one_without_writing(tmp_path: Path, capsys) -> None:
    """The CI and hook contract: 1 means prose was found, and the file is untouched."""
    path = tmp_path / "a.py"
    source = "# prose\nx = 1\n"
    path.write_text(source, encoding="utf-8")

    code = cli.main(["check", str(tmp_path)])

    assert code == 1
    assert "# prose" in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == source


def test_check_exits_zero_on_a_clean_tree(tmp_path: Path) -> None:
    """The positive control: without this, a check that always failed would pass above."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert cli.main(["check", str(tmp_path)]) == 0


def test_fix_rewrites_the_file_and_check_then_passes(tmp_path: Path) -> None:
    """Fix is the migration path, so its output must satisfy the gate it feeds."""
    path = tmp_path / "a.py"
    path.write_text('# prose\ndef f():\n    """More prose."""\n    return 1\n', encoding="utf-8")

    assert cli.main(["fix", str(tmp_path)]) == 0

    assert "prose" not in path.read_text(encoding="utf-8")
    assert cli.main(["check", str(tmp_path)]) == 0


def test_fix_leaves_a_directive_alone(tmp_path: Path) -> None:
    """A strip that removed these would break the build it is supposed to protect."""
    path = tmp_path / "a.py"
    path.write_text("#!/usr/bin/env python3\n# prose\nimport os  # noqa: F401\n", encoding="utf-8")

    cli.main(["fix", str(tmp_path)])

    text = path.read_text(encoding="utf-8")
    assert "#!/usr/bin/env python3" in text
    assert "# noqa: F401" in text
    assert "# prose" not in text


def test_a_file_the_kit_does_not_claim_is_never_visited(tmp_path: Path) -> None:
    """Config and prose keep their comments; the walk must not pick them up at all."""
    (tmp_path / "a.yaml").write_text("# a yaml comment\nkey: value\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("<!-- a prose comment -->\n", encoding="utf-8")

    assert cli.main(["check", str(tmp_path)]) == 0


def test_the_walk_skips_a_vendor_directory(tmp_path: Path) -> None:
    """A dependency tree is not the consumer's code and must not be rewritten."""
    vendored = tmp_path / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "a.js").write_text("// prose\n", encoding="utf-8")

    assert cli.main(["check", str(tmp_path)]) == 0


def test_languages_prints_the_claimed_extensions(capsys) -> None:
    """A consumer must be able to tell an unsupported file from a clean one."""
    assert cli.main(["languages"]) == 0

    printed = capsys.readouterr().out.split()
    assert ".py" in printed
    assert ".ts" in printed
    assert ".yaml" not in printed
