from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".basicly" / "core" / "hooks"
LOCAL = ("fast", "staged")
TEST_RUN = re.compile(
    r"(^|\s)(pytest|py\.test|vitest|jest)(\s|$)|\b(npm|pnpm|yarn) (run )?test\b|(^|\s)pyright(\s|$)"
)
LEDGER_PATHS = (".basicly/ledger/a.jsonl", "basicly.d/x.toml", "pyproject.toml")


def _check_runner() -> Any:
    spec = importlib.util.spec_from_file_location("budget_check_runner", HOOKS / "check_runner.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checks() -> list[dict[str, Any]]:
    found = []
    for path in [ROOT / "basicly.toml", *sorted((ROOT / "basicly.d").glob("*.toml"))]:
        found += tomllib.loads(path.read_text(encoding="utf-8")).get("verify", {}).get("checks", [])
    return found


def _command(check: dict[str, Any]) -> str:
    command = check["command"]
    return " ".join(command) if isinstance(command, list) else str(command)


def _ledger_only(check: dict[str, Any]) -> bool:
    inputs = [str(glob) for glob in check.get("inputs") or []]
    return (
        bool(check.get("local_only"))
        and bool(inputs)
        and all(
            glob.startswith((".basicly/ledger", "basicly.d/", "pyproject.toml", "basicly.toml"))
            for glob in inputs
        )
    )


def test_no_local_check_runs_a_test_suite_or_a_whole_repo_type_check() -> None:
    offenders = [
        check["name"]
        for check in _checks()
        if set(check.get("modes") or []) & set(LOCAL)
        and TEST_RUN.search(_command(check))
        and not _ledger_only(check)
    ]

    assert offenders == [], f"move these checks to mode full, CI runs them: {offenders}"


def test_the_kit_pre_commit_and_pre_push_specs_run_no_test_suite() -> None:
    hooks = (HOOKS / "hooks.yaml").read_text(encoding="utf-8")
    scripts = re.findall(r"script: ([\w.-]+)\n\s+stage: (?:pre-commit|pre-push)", hooks)

    found = [
        name for name in scripts if TEST_RUN.search((HOOKS / name).read_text(encoding="utf-8"))
    ]

    assert scripts and found == []


@pytest.mark.parametrize(
    "name",
    [
        "declared-dependencies",
        "ledger-fsck",
        "marker-families",
        "ledger-bodies",
        "pytest-live-ledger",
    ],
)
def test_a_ledger_only_check_runs_on_a_ledger_waiver_or_config_commit_only(name: str) -> None:
    runner = _check_runner()

    for path in LEDGER_PATHS:
        assert name not in runner.scoped_skips(ROOT, "fast", [path]), path
    assert name in runner.scoped_skips(ROOT, "fast", ["src/basicly/tuning.py"])
    assert name in runner.scoped_skips(ROOT, "fast", ["tests/test_hook_budget.py"])


def test_a_one_file_change_selects_only_seconds_long_local_checks() -> None:
    runner = _check_runner()
    local = {check["name"] for check in _checks() if "fast" in (check.get("modes") or [])}

    chosen = local - set(runner.scoped_skips(ROOT, "fast", ["src/basicly/tuning.py"]))

    assert chosen <= {
        "ruff",
        "ruff-format",
        "kit-deployment",
        "test-naming",
        "spend-accuracy",
        "no-comments",
    }
