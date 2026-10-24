from __future__ import annotations

import re
import subprocess  # nosec B404
import sys
from collections import Counter
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[1] / ".basicly" / "core" / "kit" / "comments" / "cli.py"
DIRECTIVES = (
    "# noqa: F401",
    "# nosec B404",
    "# type: ignore[name-defined]",
    "# pragma: no cover",
    "# fmt: on",
    "# fmt: off",
    "# fmt: skip",
)


def _run(command: str, path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, "-I", "-S", str(CLI), command, str(path)],
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize("directive", DIRECTIVES)
def test_fix_preserves_a_directive_after_prose(directive: str, tmp_path: Path) -> None:
    path = tmp_path / "source.py"
    source = f"value = 1  # initial prose  {directive}\n"
    path.write_text(source, encoding="utf-8")

    checked = _run("check", path)
    fixed = _run("fix", path)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    expected = f"value = 1  {directive}\n"
    assert path.read_text(encoding="utf-8") == expected
    assert _run("check", path).returncode == 0
    repeated = _run("fix", path)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert "rewrote 0" in repeated.stdout
    assert path.read_text(encoding="utf-8") == expected


def test_fix_preserves_independently_counted_directives(tmp_path: Path) -> None:
    path = tmp_path / "source.py"
    source = "".join(f"value = {index}  # prose  {item}\n" for index, item in enumerate(DIRECTIVES))
    path.write_text(source, encoding="utf-8")
    pattern = re.compile(r"#\s*(noqa\b|nosec\b|type:\s*ignore\b|pragma:\s*no cover\b|fmt:)")
    before = Counter(pattern.findall(source))
    assert sum(before.values()) == len(DIRECTIVES)

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    after = path.read_text(encoding="utf-8")
    assert Counter(pattern.findall(after)) == before
    assert "# prose" not in after


@pytest.mark.parametrize(
    ("source", "prose"),
    [
        (
            "from data.persistence import (  # circular import guard  # noqa: PLC0415\n"
            "    item,\n)\n",
            "# circular import guard  ",
        ),
        (
            "value = unknown()  # pending type resolution  # type: ignore[name-defined]\n",
            "# pending type resolution  ",
        ),
        (
            "value = 1  # prefix prose # noqa: E501 - retain the directive reason\n",
            "# prefix prose ",
        ),
        (
            "value = 1  # prefix prose # more prose # noqa: F401 "
            "# nosec B404 # type: ignore[name-defined]\n",
            "# prefix prose # more prose ",
        ),
    ],
)
def test_reported_consumer_cases_keep_the_exact_directive(
    source: str, prose: str, tmp_path: Path
) -> None:
    path = tmp_path / "source.py"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source.replace(prose, "")


@pytest.mark.parametrize(
    "prose",
    [
        "# add noqa later",
        "# this function needs nosec",
        "# explain type: ignore in the guide",
        "# explain pragma: no cover in the guide",
        "# use fmt when this is formatted",
        "# prefix # noqualater is ordinary prose",
    ],
)
def test_a_directive_mention_does_not_exempt_prose(prose: str, tmp_path: Path) -> None:
    path = tmp_path / "source.py"
    source = f"value = 1  {prose}\n"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == "value = 1  \n"


def test_a_string_mention_is_preserved_while_real_prose_is_removed(tmp_path: Path) -> None:
    path = tmp_path / "source.py"
    source = 'value = "# prefix # noqa: F401"  # actual prose\n'
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source.replace("# actual prose", "")


def test_a_docstring_mention_does_not_become_a_directive(tmp_path: Path) -> None:
    path = tmp_path / "source.py"
    source = "class Sample:\n    '''Prose about 'noqa: F401' and other tools.'''\n"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == "class Sample:\n    pass\n"


@pytest.mark.parametrize(
    ("suffix", "source", "expected"),
    [
        (
            "js",
            "const value = 1; // prefix prose // eslint-disable-line\n",
            "const value = 1; // eslint-disable-line\n",
        ),
        (
            "tsx",
            "const view = <Text>http://x</Text>; // prefix prose // @ts-expect-error\n",
            "const view = <Text>http://x</Text>; // @ts-expect-error\n",
        ),
        (
            "js",
            "const value = 1; // prose mentions # noqa\n",
            "const value = 1; \n",
        ),
    ],
)
def test_directive_suffixes_use_the_actual_comment_marker(
    suffix: str, source: str, expected: str, tmp_path: Path
) -> None:
    path = tmp_path / f"source.{suffix}"
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == expected


def test_uncertain_source_with_a_directive_is_refused_without_writes(tmp_path: Path) -> None:
    path = tmp_path / "source.py"
    source = 'value = "never closed  # prose # noqa: F401\n'
    path.write_text(source, encoding="utf-8")

    fixed = _run("fix", path)

    assert fixed.returncode == 2, fixed.stdout + fixed.stderr
    assert path.read_text(encoding="utf-8") == source
