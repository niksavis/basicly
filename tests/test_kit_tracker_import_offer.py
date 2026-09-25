from __future__ import annotations

import io
import itertools
import json
import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load

VENDORED = Path(".basicly") / "kit" / "tracker"
LEDGER = ".basicly/ledger"
RERUN = "basicly-tracker init --import {source}"
FOREIGN_TOOLS = ("bd", "br", "beans")

BEAN = "---\ntitle: {title}\nstatus: todo\ntype: task\n---\n\nThe body.\n"

_loaded = itertools.count()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git" / "hooks").mkdir(parents=True)
    shutil.copytree(
        REPO_ROOT / KIT_RELATIVE, root / VENDORED, ignore=shutil.ignore_patterns("__pycache__")
    )
    return root


def _beads(root: Path, count: int) -> None:
    (root / ".beads").mkdir()
    lines = [
        json.dumps({"id": f"old-a{n}", "title": f"old {n}", "status": "open", "priority": 2})
        for n in range(count)
    ]
    (root / ".beads" / "issues.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _beans(root: Path) -> None:
    (root / ".beans.yml").write_text("beans:\n  path: .beans\n", encoding="utf-8")
    (root / ".beans").mkdir()
    for bean in ("demo-aa01--first", "demo-bb02--second"):
        text = BEAN.format(title=bean.split("--")[1])
        (root / ".beans" / f"{bean}.md").write_text(text, encoding="utf-8")


def _fake_tools(tmp_path: Path) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    for tool in FOREIGN_TOOLS:
        script = tools / tool
        script.write_text(f'#!/bin/sh\ntouch "{tmp_path}/{tool}-ran"\n', encoding="utf-8")
        script.chmod(0o755)
    return tools


def _init(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    tools = _fake_tools(root.parent)
    return subprocess.run(  # nosec B603
        [
            sys.executable,
            str(root / VENDORED / "install_hook.py"),
            "--root",
            str(root),
            "--ledger",
            LEDGER,
            "--pin",
            "--import-command",
            RERUN,
            *extra,
        ],
        cwd=root,
        env={**os.environ, "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def _kit(root: Path, *argv: str) -> dict:
    done = subprocess.run(  # nosec B603
        [sys.executable, str(root / VENDORED / "cli.py"), *argv],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(done.stdout)


def _logged(root: Path) -> list[Path]:
    return sorted((root / LEDGER).glob("*.jsonl"))


def _offer(root: Path):
    return _load(root / VENDORED / "import_offer.py", f"kit_offer_test_{next(_loaded)}")


def test_a_beads_export_is_named_with_its_count_and_nothing_imports_without_a_terminal(
    repo: Path,
) -> None:
    _beads(repo, 3)

    done = _init(repo)

    assert done.returncode == 0, done.stderr
    assert "found a beads backlog: .beads/issues.jsonl holds 3 record(s)" in done.stdout
    assert "nothing was imported, because no terminal was there to answer" in done.stdout
    assert "run `basicly-tracker init --import beads`" in done.stdout
    assert "`python3 .basicly/kit/tracker/cli.py refine .basicly/ledger`" in done.stdout
    assert _logged(repo) == []


def test_import_beads_imports_every_record_into_refine_and_none_into_ready(repo: Path) -> None:
    _beads(repo, 3)

    done = _init(repo, "--import", "beads")

    assert done.returncode == 0, done.stderr
    assert "imported 3 record(s) from .beads/issues.jsonl into .basicly/ledger" in done.stdout
    assert "lands in refine, not in ready" in done.stdout
    assert _kit(repo, "ready", LEDGER)["count"] == 0
    assert _kit(repo, "refine", LEDGER)["count"] == 3


def test_without_a_terminal_a_backlog_the_ledger_already_holds_is_not_offered_again(
    repo: Path,
) -> None:
    _beads(repo, 3)
    _init(repo, "--import", "beads")

    again = _init(repo)

    assert again.returncode == 0, again.stderr
    assert "the beads backlog at .beads/issues.jsonl is already in the ledger (3 record(s))" in (
        again.stdout
    )
    assert "found a beads backlog" not in again.stdout
    assert "--import beads`" not in again.stdout


@pytest.mark.parametrize("store", [".beads/dolt", ".beads/embeddeddolt"])
def test_a_bd_dolt_store_without_an_export_says_to_export_it_first(repo: Path, store: str) -> None:
    (repo / store).mkdir(parents=True)

    done = _init(repo)

    assert done.returncode == 0, done.stderr
    assert f"found a bd Dolt store at {store} and no .beads/issues.jsonl" in done.stdout
    assert "run `bd export -o .beads/issues.jsonl` first" in done.stdout
    assert "then run `basicly-tracker init --import beads`" in done.stdout
    assert _logged(repo) == []


def test_import_beads_over_a_dolt_store_is_refused_until_it_is_exported(repo: Path) -> None:
    (repo / ".beads" / "dolt").mkdir(parents=True)

    done = _init(repo, "--import", "beads")

    assert done.returncode != 0
    assert "--import beads found a bd Dolt store" in done.stderr
    assert "`bd export -o .beads/issues.jsonl` first" in done.stderr
    assert not (repo / LEDGER).exists()


def test_a_beans_backlog_is_named_with_its_bean_count_and_imports_on_the_flag(
    repo: Path,
) -> None:
    _beans(repo)

    offered = _init(repo)
    imported = _init(repo, "--import", "beans")

    assert "found a beans backlog: .beans holds 2 bean(s)" in offered.stdout
    assert "run `basicly-tracker init --import beans`" in offered.stdout
    assert imported.returncode == 0, imported.stderr
    assert "imported 2 record(s) from .beans into .basicly/ledger" in imported.stdout
    assert _kit(repo, "refine", LEDGER)["count"] == 2


def test_a_beans_config_without_bean_files_says_to_name_the_folder(repo: Path) -> None:
    (repo / ".beans.yml").write_text("beans:\n  path: work\n", encoding="utf-8")

    done = _init(repo)

    assert "found .beans.yml and no bean file under .beans" in done.stdout
    assert (
        "`python3 .basicly/kit/tracker/cli.py import .basicly/ledger <the beans folder> "
        "--from beans`" in done.stdout
    )


def test_import_of_a_source_that_is_absent_is_refused_by_name(repo: Path) -> None:
    _beads(repo, 1)

    done = _init(repo, "--import", "beans")

    assert done.returncode != 0
    assert "--import beans names a beans backlog" in done.stderr
    assert "no bean file under .beans and no .beans.yml" in done.stderr
    assert not (repo / LEDGER).exists()


def test_a_repository_without_a_backlog_hears_no_offer(repo: Path) -> None:
    done = _init(repo)

    assert done.returncode == 0, done.stderr
    assert "found" not in done.stdout
    assert "refine" not in done.stdout


def test_detection_runs_no_foreign_tracker(repo: Path) -> None:
    _beads(repo, 2)
    _beans(repo)

    _init(repo, "--import", "beads")

    assert sorted(path.name for path in repo.parent.glob("*-ran")) == []
    tools = repo.parent / "tools"
    for tool in FOREIGN_TOOLS:
        subprocess.run([str(tools / tool)], check=True)  # nosec B603
    assert len(list(repo.parent.glob("*-ran"))) == len(FOREIGN_TOOLS)


def _at_terminal(repo: Path, answer: str | None):
    offer = _offer(repo)
    (repo / LEDGER).mkdir(parents=True, exist_ok=True)
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        if answer is None:
            raise EOFError
        return answer

    stream = io.StringIO()
    install = offer.Install(repo, repo / LEDGER, import_command=RERUN)
    offer.offer_import(install, offer.find_backlogs(repo), ask=ask, stream=stream)
    return asked, stream.getvalue()


def test_at_a_terminal_a_yes_imports_after_a_dry_run_summary(repo: Path) -> None:
    _beads(repo, 3)

    asked, out = _at_terminal(repo, "y")

    assert len(asked) == 1
    planned = out.index("a dry run would import 3 record(s) from .beads/issues.jsonl")
    assert planned < out.index("tracker: imported 3 record(s)")
    assert _kit(repo, "refine", LEDGER)["count"] == 3


@pytest.mark.parametrize("answer", ["n", "", "nope", None])
def test_at_a_terminal_anything_but_yes_imports_nothing(repo: Path, answer: str | None) -> None:
    _beads(repo, 3)

    asked, out = _at_terminal(repo, answer)

    assert len(asked) == 1
    assert "a dry run would import 3 record(s)" in out
    assert (
        "nothing was imported; to import it later, run `basicly-tracker init --import beads`" in out
    )
    assert _logged(repo) == []


def test_at_a_terminal_a_backlog_the_ledger_already_holds_is_not_asked_again(
    repo: Path,
) -> None:
    _beads(repo, 2)
    _at_terminal(repo, "y")

    asked, out = _at_terminal(repo, "y")

    assert asked == []
    assert "the beads backlog at .beads/issues.jsonl is already in the ledger (2 record(s))" in out
    assert "found a beads backlog" not in out


@pytest.mark.parametrize(
    ("chosen", "dry_run", "why"),
    [("", True, "this is a dry run"), ("beads", False, "--import named beads")],
)
def test_at_a_terminal_a_dry_run_or_a_named_source_asks_nothing(
    repo: Path, chosen: str, dry_run: bool, why: str
) -> None:
    _beads(repo, 1)
    _beans(repo)
    offer = _offer(repo)
    (repo / LEDGER).mkdir(parents=True)
    asked: list[str] = []
    stream = io.StringIO()
    install = offer.Install(repo, repo / LEDGER, chosen, RERUN, dry_run)

    offer.offer_import(
        install, offer.find_backlogs(repo), ask=lambda p: asked.append(p) or "y", stream=stream
    )

    assert asked == []
    assert f"nothing was imported, because {why}; to import it, run" in stream.getvalue()
    assert len(_logged(repo)) == (0 if dry_run else 1)
