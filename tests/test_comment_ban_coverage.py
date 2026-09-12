from __future__ import annotations

import fnmatch
import importlib.util
import subprocess  # nosec B404
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = (
    REPO_ROOT
    / ".basicly"
    / "core"
    / "fragments"
    / "project"
    / "code-is-authoritative.fragment.yaml"
)
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "comments"


def _scan():
    spec = importlib.util.spec_from_file_location("ban_scan", KIT / "scan.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ban_scan"] = module
    spec.loader.exec_module(module)
    return module


scan = _scan()

SKIPPED = frozenset({"vendor", "node_modules", "__pycache__"})


def _scoped_paths() -> list[str]:
    source = yaml.safe_load(FRAGMENT.read_text(encoding="utf-8"))
    return list(source["scope"]["paths"])


def _covered_tracked_files() -> list[str]:
    listed = subprocess.run(  # nosec B603 B607
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [
        name
        for name in listed
        if scan.is_covered(Path(name)) and not any(part in SKIPPED for part in Path(name).parts)
    ]


def test_every_file_the_ban_gates_sits_under_a_path_the_rule_scopes() -> None:
    patterns = _scoped_paths()
    tracked = _covered_tracked_files()
    assert tracked, "the sweep found no code files, so it discriminates nothing"

    unscoped = sorted({
        str(Path(name).parent)
        for name in tracked
        if not any(fnmatch.fnmatch(name, pattern) for pattern in patterns)
    })

    assert unscoped == [], (
        "these directories hold code the no-comments gate refuses, but no agent is told the "
        "rule because code-is-authoritative does not scope them; add each to scope.paths in "
        f"{FRAGMENT.relative_to(REPO_ROOT)}: {unscoped}"
    )


def test_the_sweep_reports_a_directory_no_scope_covers() -> None:
    tracked = ["src/basicly/cli.py", "elsewhere/thing.py"]
    patterns = ["src/**"]

    unscoped = sorted({
        str(Path(name).parent)
        for name in tracked
        if not any(fnmatch.fnmatch(name, pattern) for pattern in patterns)
    })

    assert unscoped == ["elsewhere"]


def test_the_rule_states_the_ban_and_names_the_gate_that_enforces_it() -> None:
    body = yaml.safe_load(FRAGMENT.read_text(encoding="utf-8"))["body"]

    assert "no comment, no docstring" in body.lower()
    assert "no-comments" in body
    assert "noqa" in body, "the rule must say a directive survives, or it reads as a total ban"
