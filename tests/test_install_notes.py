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


def test_a_config_that_already_excludes_the_core_is_not_advised(tmp_path: Path) -> None:
    _repo(
        tmp_path,
        {
            ".ruff.toml": 'extend-exclude = [".basicly/core"]\n',
            ".prettierignore": ".basicly/core/\n",
            "pyproject.toml": '[tool.ruff]\nextend-exclude = [".basicly/core"]\n',
            ".pre-commit-config.yaml": "repos:\n  - hooks:\n      - exclude: ^\\.basicly/core/\n",
        },
    )

    assert install_notes(tmp_path) == [], (
        "advising a change the repository has already made is unactionable, and it sits "
        "in the same tail of the install output as a tracked CI edit"
    )


def test_the_same_configs_without_the_exclusion_are_still_advised(tmp_path: Path) -> None:
    _repo(
        tmp_path,
        {
            ".ruff.toml": "line-length = 88\n",
            ".prettierignore": "dist/\n",
            "pyproject.toml": "[tool.ruff]\nline-length = 88\n",
            ".pre-commit-config.yaml": "repos: []\n",
        },
    )

    notes = "\n".join(install_notes(tmp_path))

    assert ".ruff.toml" in notes, (
        "the control: without this the fix above is a suppression rather than a check"
    )
    assert ".prettierignore" in notes
    assert "pyproject.toml" in notes
    assert ".pre-commit-config.yaml" in notes


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


PER_FILE_IGNORES = '[lint.per-file-ignores]\n".basicly/core/hooks/**" = ["S"]\n'


def test_a_per_file_ignore_naming_the_core_is_not_an_exclusion(tmp_path: Path) -> None:
    (tmp_path / ".ruff.toml").write_text(PER_FILE_IGNORES, encoding="utf-8")

    notes = "\n".join(install_notes(tmp_path))

    assert ".ruff.toml" in notes


def test_an_exclusion_beside_a_per_file_ignore_stays_silent(tmp_path: Path) -> None:
    (tmp_path / ".ruff.toml").write_text(
        f'extend-exclude = [".basicly/core"]\n{PER_FILE_IGNORES}', encoding="utf-8"
    )

    assert install_notes(tmp_path) == []


def test_a_commented_path_is_not_an_exclusion(tmp_path: Path) -> None:
    (tmp_path / ".prettierignore").write_text("# .basicly/core/\n", encoding="utf-8")

    assert ".prettierignore" in "\n".join(install_notes(tmp_path))


def test_a_pre_commit_exclude_line_silences_it(tmp_path: Path) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - hooks:\n      - id: x\n        exclude: ^\\.basicly/core/\n", encoding="utf-8"
    )

    assert install_notes(tmp_path) == []


def test_a_hook_id_naming_the_core_is_not_an_exclusion(tmp_path: Path) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - hooks:\n      - id: lint-basicly/core\n", encoding="utf-8"
    )

    assert ".pre-commit-config.yaml" in "\n".join(install_notes(tmp_path))


def test_a_pyproject_ruff_table_is_read_for_an_exclusion_not_a_mention(tmp_path: Path) -> None:
    mentions = '[tool.ruff]\nsrc = [".basicly/core"]\n'
    (tmp_path / "pyproject.toml").write_text(mentions, encoding="utf-8")
    assert "pyproject.toml" in "\n".join(install_notes(tmp_path))

    (tmp_path / "pyproject.toml").write_text(
        f'{mentions}extend-exclude = [".basicly/core"]\n', encoding="utf-8"
    )
    assert install_notes(tmp_path) == []
