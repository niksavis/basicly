"""What `basicly install` tells a consumer it will not change on their behalf.

A consumer's own linters cover the repo root, so they reach into the vendored catalog:
one reported 1192 E501 inside `.basicly/core/**` and their formatters rewrote 75 of our
files. Install cannot edit their config, so it names the exclusion (basicly-8cd7wo5).
"""

from __future__ import annotations

from pathlib import Path

from basicly.scaffolds import install_notes


def _repo(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        (root / name).write_text(body, encoding="utf-8")
    return root


def test_a_repo_with_no_tooling_of_its_own_is_told_nothing(tmp_path: Path) -> None:
    """The positive control: the notes are about the repo, not printed on every install."""
    assert install_notes(tmp_path) == []


def test_each_tooling_config_present_names_its_own_exclusion(tmp_path: Path) -> None:
    """Naming the file and the line is the whole fix; editing it is the consumer's call."""
    _repo(
        tmp_path,
        {
            ".pre-commit-config.yaml": "repos: []\n",
            ".prettierignore": "dist/\n",
            "pyproject.toml": "[tool.ruff]\nline-length = 88\n",
        },
    )

    notes = "\n".join(install_notes(tmp_path))

    assert "Exclude `.basicly/core/`" in notes
    assert ".pre-commit-config.yaml: add `exclude: " in notes
    assert ".prettierignore: add a `.basicly/core/` line" in notes
    assert 'extend-exclude = [".basicly/core"]` under [tool.ruff]' in notes


def test_a_pyproject_without_ruff_is_not_named(tmp_path: Path) -> None:
    """Every Python repo has a pyproject; only one that configures ruff has the problem."""
    _repo(tmp_path, {"pyproject.toml": '[project]\nname = "x"\n'})

    assert install_notes(tmp_path) == []


def test_a_root_claude_md_is_named_as_a_second_always_on_file(tmp_path: Path) -> None:
    """Claude Code loads both, and only `.claude/CLAUDE.md` is a projection target.

    So install neither overwrote a root one nor mentioned it, and the consumer ended
    with two always-on instruction files and no notice.
    """
    _repo(tmp_path, {"CLAUDE.md": "# mine\n"})

    notes = "\n".join(install_notes(tmp_path))

    assert "Claude Code loads both" in notes
    assert "Nothing overwrote yours" in notes


def test_a_configured_secret_scanner_is_told_to_exclude_the_ledger(tmp_path: Path) -> None:
    """Every imported record carries a sha256 digest, and every scanner flags 64-char hex.

    A consumer's 702-record import gave them 702 hits and a blocked commit. A baseline
    is the wrong instrument: the digests are rewritten on every import.
    """
    _repo(tmp_path, {".secrets.baseline": "{}\n"})

    notes = "\n".join(install_notes(tmp_path))

    assert "Exclude `.basicly/ledger/`" in notes
    assert ".secrets.baseline: detect-secrets" in notes


def test_a_scanner_named_only_in_the_pre_commit_config_is_found(tmp_path: Path) -> None:
    """Most repos configure it there and carry no standalone file of its own."""
    _repo(tmp_path, {".pre-commit-config.yaml": "repos:\n  - repo: gitleaks\n"})

    notes = "\n".join(install_notes(tmp_path))

    assert ".pre-commit-config.yaml: gitleaks" in notes


def test_a_repo_with_no_scanner_is_told_nothing_about_the_ledger(tmp_path: Path) -> None:
    """The positive control: the note is about the repo, not printed on every install."""
    _repo(tmp_path, {".pre-commit-config.yaml": "repos: []\n"})

    assert "Exclude `.basicly/ledger/`" not in "\n".join(install_notes(tmp_path))
