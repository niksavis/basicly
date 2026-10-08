from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import sys
from collections import Counter
from pathlib import Path

from basicly import __version__, owned_store, state, tracker, tracker_query
from tests import flipped_tracker

REPO_ROOT = Path(__file__).resolve().parent.parent

RECORD = "rs-1"

FOUR_MIB = 4 * 1024 * 1024

CONTRACT_ITEM_KEYS = {
    "id",
    "title",
    "status",
    "rawStatus",
    "priority",
    "type",
    "assignee",
    "updatedAt",
    "source",
}

CONTRACT_STATUSES = ("open", "in_progress", "blocked", "deferred", "closed", "other")

READ_COMMANDS = (
    ("tracker", "list"),
    ("tracker", "list", "--status", "open"),
    ("tracker", "show", RECORD),
    ("tracker", "ready", "--json"),
    ("tracker", "blocked", "--json"),
    ("tracker", "stats", "--json"),
    ("tracker", "items", "--json"),
    ("tracker", "describe", "--json"),
)

A_WRITE = ("tracker", "write", "--", "comments", "add", RECORD, "a planted kit runs on a write")


def _basicly(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    return subprocess.run(  # nosec B603
        [sys.executable, "-m", "basicly.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _seeded_repo(tmp_path: Path) -> Path:
    repo = flipped_tracker.flipped_repo(tmp_path / "repo")
    flipped_tracker.seed(repo, RECORD, title="the planted root", priority="1")
    return repo


def _plant(repo: Path, markers: Path, modules: tuple[str, ...] = ()) -> list[Path]:
    markers.mkdir(exist_ok=True)
    kit_dir = repo / tracker.KIT_TRACKER_DIR
    planted = [kit_dir / f"{name}.py" for name in modules] or sorted(kit_dir.glob("*.py"))
    for module in planted:
        marker = markers / module.stem
        with module.open("a", encoding="utf-8") as handle:
            handle.write(f"\nimport pathlib as _planted\n_planted.Path({str(marker)!r}).touch()\n")
    return planted


def _ran(markers: Path) -> list[str]:
    return sorted(path.name for path in markers.iterdir())


def _ledger_bytes(repo: Path) -> dict[str, bytes]:
    ledger = owned_store.ledger_dir(repo)
    return {path.name: path.read_bytes() for path in sorted(ledger.iterdir()) if path.is_file()}


def test_read_only_commands_run_no_repository_code(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path)
    markers = tmp_path / "markers"
    planted = _plant(repo, markers)
    assert len(planted) > 10

    for command in READ_COMMANDS:
        result = _basicly(repo, *command)
        assert result.returncode == 0, (command, result.stderr)
        assert _ran(markers) == [], command

    control = _basicly(repo, *A_WRITE)
    assert control.returncode == 0, control.stderr
    assert _ran(markers), "the planted kit never ran, so its absence above proves nothing"


def test_items_read_path_runs_no_repository_code(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path)
    markers = tmp_path / "markers"
    _plant(repo, markers, ("queries", "values", "snapshot"))

    items = _basicly(repo, "tracker", "items", "--json", "--status", "open")
    described = _basicly(repo, "tracker", "describe", "--json")

    assert items.returncode == 0, items.stderr
    assert described.returncode == 0, described.stderr
    assert [item["id"] for item in json.loads(items.stdout)] == [RECORD]
    assert _ran(markers) == []
    assert _basicly(repo, *A_WRITE).returncode == 0
    assert _ran(markers), "the planted kit never ran, so its absence above proves nothing"


def test_items_json_is_normalized_and_small(work_repo: Path) -> None:
    every = [arg for status in CONTRACT_STATUSES for arg in ("--status", status)]

    result = _basicly(work_repo, "tracker", "items", "--json", *every)
    default = _basicly(work_repo, "tracker", "items", "--json")
    stats = _basicly(work_repo, "tracker", "stats", "--json")

    assert result.returncode == 0, result.stderr
    assert len(result.stdout.encode("utf-8")) < FOUR_MIB
    items = json.loads(result.stdout)
    totals = json.loads(stats.stdout)
    assert len(items) == totals["records"]
    assert Counter(item["rawStatus"] or "unset" for item in items) == totals["by_status"]
    assert len(items) > 1000
    ids = [item["id"] for item in items]
    assert ids == sorted(set(ids))
    status_map = tracker_query.status_map()
    for item in items:
        assert set(item) == CONTRACT_ITEM_KEYS, item["id"]
        assert item["source"] == "basicly"
        assert item["status"] == status_map.get(str(item["rawStatus"]), "other")
        assert item["priority"] is None or isinstance(item["priority"], int)
        assert isinstance(item["title"], str)
    assert default.returncode == 0, default.stderr
    assert [item["id"] for item in json.loads(default.stdout)] == [
        item["id"] for item in items if item["status"] in tracker_query.OPEN_STATUSES
    ]


def test_items_json_on_a_seeded_record_carries_its_fields(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path)

    result = _basicly(repo, "tracker", "items", "--json")

    assert result.returncode == 0, result.stderr
    [item] = json.loads(result.stdout)
    assert item["id"] == RECORD
    assert item["title"] == "the planted root"
    assert item["status"] == "open"
    assert item["rawStatus"] == "open"
    assert item["priority"] == 1
    assert item["updatedAt"]


def test_items_without_a_ledger_is_refused_by_name(tmp_path: Path) -> None:
    result = _basicly(tmp_path, "tracker", "items", "--json")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "there is no ledger at .basicly/ledger" in result.stderr
    assert "`basicly install`" in result.stderr


def test_describe_json_matches_contract_v1(tmp_path: Path) -> None:
    result = _basicly(tmp_path, "tracker", "describe", "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "name": "basicly",
        "version": __version__,
        "contract": 1,
        "watch": [".basicly/ledger/*.jsonl"],
        "writes": [["tracker", "write"]],
        "statusMap": {
            "open": "open",
            "in_progress": "in_progress",
            "blocked": "blocked",
            "deferred": "deferred",
            "closed": "closed",
        },
    }


def test_an_older_repository_kit_is_refused_by_name(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path)
    state.write_install_state(repo / ".basicly" / "state" / "install.json", "0.20.2", {})
    markers = tmp_path / "markers"
    _plant(repo, markers)
    before = _ledger_bytes(repo)

    refused = _basicly(repo, *A_WRITE)

    assert refused.returncode == 1
    assert "basicly 0.20.2" in refused.stderr
    assert f"basicly {__version__}" in refused.stderr
    assert "`basicly install`" in refused.stderr
    assert "unexpected keyword" not in refused.stderr
    assert _ran(markers) == []
    assert _ledger_bytes(repo) == before
    assert _basicly(repo, "tracker", "items", "--json").returncode == 0


def test_a_repository_kit_of_the_engine_version_is_admitted(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path)
    state.write_install_state(repo / ".basicly" / "state" / "install.json", __version__, {})

    landed = _basicly(repo, *A_WRITE)

    assert landed.returncode == 0, landed.stderr
    assert "recorded" in landed.stdout


def test_session_start_reads_a_repository_on_an_older_kit(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path)
    state.write_install_state(repo / ".basicly" / "state" / "install.json", "0.20.2", {})

    result = _basicly(repo, "session", "start", "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["tracker"]["present"] is True
