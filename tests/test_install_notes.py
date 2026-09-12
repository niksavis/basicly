from __future__ import annotations

from pathlib import Path

from basicly.scaffolds import install_notes


def _repo(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        (root / name).write_text(body, encoding="utf-8")
    return root


def test_a_repo_with_no_tooling_of_its_own_is_told_nothing(tmp_path: Path) -> None:
    assert install_notes(tmp_path) == []


def test_each_tooling_config_present_names_its_own_exclusion(tmp_path: Path) -> None:
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
    _repo(tmp_path, {"pyproject.toml": '[project]\nname = "x"\n'})

    assert install_notes(tmp_path) == []


def test_a_root_claude_md_is_named_as_a_second_always_on_file(tmp_path: Path) -> None:

    _repo(tmp_path, {"CLAUDE.md": "# mine\n"})

    notes = "\n".join(install_notes(tmp_path))

    assert "Claude Code loads both" in notes
    assert "Nothing overwrote yours" in notes


def test_a_configured_secret_scanner_is_told_to_exclude_the_ledger(tmp_path: Path) -> None:

    _repo(tmp_path, {".secrets.baseline": "{}\n"})

    notes = "\n".join(install_notes(tmp_path))

    assert "Exclude `.basicly/ledger/`" in notes
    assert ".secrets.baseline: detect-secrets" in notes


def test_a_scanner_named_only_in_the_pre_commit_config_is_found(tmp_path: Path) -> None:
    _repo(tmp_path, {".pre-commit-config.yaml": "repos:\n  - repo: gitleaks\n"})

    notes = "\n".join(install_notes(tmp_path))

    assert ".pre-commit-config.yaml: gitleaks" in notes


def test_a_repo_with_no_scanner_is_told_nothing_about_the_ledger(tmp_path: Path) -> None:
    _repo(tmp_path, {".pre-commit-config.yaml": "repos: []\n"})

    assert "Exclude `.basicly/ledger/`" not in "\n".join(install_notes(tmp_path))
