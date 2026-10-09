from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.test_secret_scan import GITHUB, PK, _git, _repo, _run_hook, scan

if TYPE_CHECKING:
    from pathlib import Path

EXCLUDE = "exclude: ^mods/[^/]+/\\.claude-plugin/types/\n"
GENERATED = "mods/one/.claude-plugin/types/index.d.ts"
DOC_COMMENT = "/** Server-attested source token" + ': "github_webhook" | "trigger_fire" */\n'


def _stage(repo: Path, path: str, text: str, config: str = EXCLUDE) -> None:
    (repo / ".pre-commit-config.yaml").write_text(config + "repos: []\n", encoding="utf-8")
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(repo, "add", path)


def test_generic_assignment_passes_in_an_excluded_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _stage(repo, GENERATED, DOC_COMMENT)
    _stage(repo, "mods/one/src/index.ts", DOC_COMMENT)
    result = _run_hook(repo)
    assert result.returncode == 1
    assert "mods/one/src/index.ts:1: generic-secret-assignment" in result.stderr
    assert GENERATED not in result.stderr


@pytest.mark.parametrize(("secret", "rule"), [(GITHUB, "github-token"), (PK, "private-key")])
def test_specific_secret_is_refused_in_an_excluded_path(
    tmp_path: Path, secret: str, rule: str
) -> None:
    repo = _repo(tmp_path)
    _stage(repo, GENERATED, f'const value = "{secret}";\n')
    result = _run_hook(repo)
    assert result.returncode == 1
    assert f"{GENERATED}:1: {rule}" in result.stderr


def test_an_invalid_exclude_regex_refuses_by_name(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _stage(repo, "app.py", "total = 1\n", config="exclude: ^mods/(\n")
    result = _run_hook(repo)
    assert result.returncode == 1
    assert "the top-level exclude in .pre-commit-config.yaml is not a valid regex" in result.stderr


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ("exclude: ^gen/  # generated\nrepos: []\n", "^gen/"),
        ("exclude: '^gen/it''s/'\n", "^gen/it's/"),
        ('exclude: "^gen\\\\.d/"\n', "^gen\\.d/"),
        ("exclude: |\n  (?x)^(\n    gen/\n  )$\nrepos: []\n", "(?x)^(\ngen/\n)$"),
        ("repos:\n- repo: local\n  hooks:\n  - id: a\n    exclude: ^gen/\n", None),
    ],
)
def test_the_top_level_exclude_is_read_in_each_yaml_form(config: str, expected: str | None) -> None:
    assert scan.precommit_exclude(config) == expected
