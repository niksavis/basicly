from __future__ import annotations

from pathlib import Path

import pytest

from basicly import markdownlint
from tests.markdownlint_runtime_fixture import provision_markdownlint_double


def test_bundled_checker_refuses_even_when_an_installed_hook_would_pass(tmp_path: Path) -> None:
    installed = tmp_path / ".basicly/core/hooks/markdownlint.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("raise SystemExit(0)\n", encoding="utf-8")

    with pytest.raises(ValueError, match="npm install --save-dev markdownlint-cli2"):
        markdownlint.require_prerequisites(tmp_path, None)


def test_bundled_checker_accepts_explicit_tools_without_running_the_linter(tmp_path: Path) -> None:
    provision_markdownlint_double(tmp_path)
    entry = tmp_path / "node_modules/markdownlint-cli2/markdownlint-cli2-bin.mjs"
    entry.write_text("process.exit(3);\n", encoding="utf-8")

    assert markdownlint.require_prerequisites(tmp_path, None) is None


def test_non_node_selection_does_not_invoke_a_prerequisite_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        markdownlint.checkout, "run", lambda *_args, **_kwargs: pytest.fail("checker was invoked")
    )
    assert markdownlint.require_prerequisites(tmp_path, frozenset({"python"})) is None
