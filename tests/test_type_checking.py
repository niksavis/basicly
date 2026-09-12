from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

_REPO_ROOT = Path(__file__).resolve().parents[1]

_TYPE_ERROR_MODULE = 'def count() -> int:\n    return "not an int"\n'


def _pyright_settings() -> dict[str, list[str] | str]:
    config = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["pyright"]


def _tracked_python() -> list[PurePosixPath]:

    listing = subprocess.run(
        ["git", "ls-files", "--", "*.py"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [PurePosixPath(line) for line in listing.stdout.splitlines()]


def _unanalysed(settings: dict[str, list[str] | str], paths: list[PurePosixPath]) -> list[str]:

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
    body = "\n".join(f"{key} = {json.dumps(value)}" for key, value in settings.items())
    return f"[tool.pyright]\n{body}\n"


def test_pyright_analyses_every_tracked_python_directory() -> None:

    tracked = _tracked_python()
    assert tracked, "the sweep found no tracked Python"

    unanalysed = _unanalysed(_pyright_settings(), tracked)

    assert not unanalysed, (
        "tracked Python outside pyright's include list; add each directory to "
        f"[tool.pyright] include in pyproject.toml: {unanalysed}"
    )


def test_the_coverage_sweep_reports_a_directory_no_include_covers() -> None:
    unanalysed = _unanalysed(
        {"include": ["src"], "exclude": []},
        [PurePosixPath("src/basicly/cli.py"), PurePosixPath(".scripts/docs_claims.py")],
    )

    assert unanalysed == [".scripts"]


def test_the_coverage_sweep_reports_a_directory_the_default_exclude_drops() -> None:

    unanalysed = _unanalysed(
        {"include": ["src", ".scripts"], "exclude": ["**/.*"]},
        [PurePosixPath("src/basicly/cli.py"), PurePosixPath(".scripts/docs_claims.py")],
    )

    assert unanalysed == [".scripts"]


def test_pyright_fails_on_a_type_error_under_a_dot_directory(tmp_path: Path) -> None:

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
            check=False,
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
