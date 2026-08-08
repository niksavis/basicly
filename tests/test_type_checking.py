"""The type checker's coverage of the Python this repo ships (basicly-u2hl.15).

pyright's default ``exclude`` is ``["**/node_modules", "**/__pycache__", "**/.*"]``,
and that last pattern hid ``.scripts`` and ``.basicly/core`` — the hooks that ship to
consumers via ``basicly install`` and run in the dispatch path — from *every* mode the
repo runs. Nothing failed, because a type checker cannot report on a file it never
opened, which is the same silent-green shape the bandit sweep in ``test_verify.py``
exists to remove.

The sweep below is a config assertion rather than a pyright run: it reproduces
pyright's include/exclude semantics over the tracked tree, which is exact for the two
literal directory patterns this repo uses and costs no subprocess. The behavioural half
— that being included actually makes a bad module *fail* — is the last test.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

_REPO_ROOT = Path(__file__).resolve().parents[1]

_TYPE_ERROR_MODULE = 'def count() -> int:\n    return "not an int"\n'


def _pyright_settings() -> dict[str, list[str] | str]:
    """This repo's committed ``[tool.pyright]`` table."""
    config = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["pyright"]


def _tracked_python() -> list[PurePosixPath]:
    """Every tracked ``.py`` file in the repo.

    Tracked files rather than a directory walk, on the same grounds as the bandit
    sweep: an untracked scratch file is not something the repo ships, and would
    otherwise fail a gate about what does.
    """
    listing = subprocess.run(
        ["git", "ls-files", "--", "*.py"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [PurePosixPath(line) for line in listing.stdout.splitlines()]


def _unanalysed(settings: dict[str, list[str] | str], paths: list[PurePosixPath]) -> list[str]:
    """Every directory holding one of *paths* that pyright would not analyse.

    Mirrors pyright's own rule: a file is analysed when an ``include`` entry is it or
    one of its parents, *and* no ``exclude`` pattern matches it or a directory above
    it. ``exclude`` is applied on top of ``include`` and wins.
    """
    include = [PurePosixPath(entry) for entry in settings["include"]]
    exclude = list(settings["exclude"])

    def analysed(path: PurePosixPath) -> bool:
        included = any(path == root or root in path.parents for root in include)
        dropped = any(
            path.full_match(pattern) or path.full_match(f"{pattern}/**") for pattern in exclude
        )
        return included and not dropped

    return sorted({str(path.parent) for path in paths if not analysed(path)})


def _config(settings: dict[str, list[str] | str]) -> str:
    """A pyproject carrying *settings* verbatim as its ``[tool.pyright]`` table."""
    body = "\n".join(f"{key} = {json.dumps(value)}" for key, value in settings.items())
    return f"[tool.pyright]\n{body}\n"


def test_pyright_analyses_every_tracked_python_directory() -> None:
    """No directory of this repo's Python may sit outside the type check.

    The sweep is the whole tracked tree rather than a list of harness roots, so the
    *next* directory of first-party Python fails here instead of inheriting the
    silence ``.scripts`` and ``.basicly/core`` sat in.
    """
    tracked = _tracked_python()
    assert tracked, "the sweep found no tracked Python"

    unanalysed = _unanalysed(_pyright_settings(), tracked)

    assert not unanalysed, (
        "tracked Python outside pyright's include list; add each directory to "
        f"[tool.pyright] include in pyproject.toml: {unanalysed}"
    )


def test_the_coverage_sweep_reports_a_directory_no_include_covers() -> None:
    """The control for the sweep above: an unnamed directory has to be reported."""
    unanalysed = _unanalysed(
        {"include": ["src"], "exclude": []},
        [PurePosixPath("src/basicly/cli.py"), PurePosixPath(".scripts/docs_claims.py")],
    )

    assert unanalysed == [".scripts"]


def test_the_coverage_sweep_reports_a_directory_the_default_exclude_drops() -> None:
    """The second control, in the exact shape this bead was filed for.

    Naming a directory in ``include`` is not enough when ``exclude`` still carries the
    default ``**/.*``, so the sweep has to fail on that combination too — otherwise it
    would pass on the very config that hid these trees.
    """
    unanalysed = _unanalysed(
        {"include": ["src", ".scripts"], "exclude": ["**/.*"]},
        [PurePosixPath("src/basicly/cli.py"), PurePosixPath(".scripts/docs_claims.py")],
    )

    assert unanalysed == [".scripts"]


def test_pyright_fails_on_a_type_error_under_a_dot_directory(tmp_path: Path) -> None:
    """Being inside ``include`` has to make a bad module *fail* the check.

    Coverage in the config is necessary and not sufficient, so this runs pyright over
    a tree shaped like the repo's, carrying the committed settings verbatim and one
    module with a return-type error under ``.scripts``.

    The same tree with ``exclude`` dropped is the discriminator: pyright then falls
    back to its default, analyses nothing, and exits 0 — the silent green this change
    removes, and the reason ``include`` alone would not have been a fix.

    The probe's directory is created independently of ``include`` rather than as a side
    effect of iterating it: were ``.scripts`` dropped from the config, deriving it from
    ``include`` would leave the module unwritten and this test would die of
    ``FileNotFoundError`` during setup instead of reporting the coverage it lost.
    """
    settings = _pyright_settings()
    probe = tmp_path / ".scripts/type_error_probe.py"
    for entry in settings["include"]:
        (tmp_path / entry).mkdir(parents=True, exist_ok=True)
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(_TYPE_ERROR_MODULE, encoding="utf-8")

    def check(table: dict[str, list[str] | str]) -> tuple[int, dict[str, object]]:
        (tmp_path / "pyproject.toml").write_text(_config(table), encoding="utf-8")
        completed = subprocess.run(
            ["pyright", "--outputjson"],
            cwd=tmp_path,
            check=False,  # a non-zero exit is the assertion, not an error
            capture_output=True,
            text=True,
        )
        return completed.returncode, json.loads(completed.stdout)["summary"]

    returncode, summary = check(settings)
    default_exclude = {key: value for key, value in settings.items() if key != "exclude"}
    without_exclude_returncode, without_exclude = check(default_exclude)

    assert returncode != 0, f"the bad module under .scripts passed the check: {summary}"
    assert summary["errorCount"] == 1
    assert without_exclude_returncode == 0
    assert without_exclude["filesAnalyzed"] == 0, (
        "the discriminator analysed files, so the exclude override is not what carries "
        f"the coverage: {without_exclude}"
    )
