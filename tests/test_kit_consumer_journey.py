from __future__ import annotations

import http.client
import json
import re
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load
from tests.test_kit_consumer_install import _from_wheel, _wheel

writers = _load(REPO_ROOT / KIT_RELATIVE / "writers.py", "consumer_journey_writers")

LEDGER = ".basicly/ledger"
TRACKER_CLI = Path(".basicly") / "kit" / "tracker" / "cli.py"
BOARD_SERVER = Path(".basicly") / "kit" / "board" / "server.py"
BARE = (sys.executable, "-I", "-S")
BEAN = "---\ntitle: {title}\nstatus: todo\ntype: task\n---\n\nThe body.\n"
BEANS = {"demo-aa01": "first bean", "demo-bb02": "second bean"}
BANNER = re.compile(r"board: http://127\.0\.0\.1:(\d+)/ serves ")
SERVE_TIMEOUT_SECONDS = 30.0
POLL_SECONDS = 0.05


def _fresh_repository(tmp_path: Path) -> Path:
    repo = tmp_path / "consumer"
    (repo / ".beans").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)  # nosec B603 B607
    (repo / ".beans.yml").write_text("beans:\n  path: .beans\n", encoding="utf-8")
    for bean, title in BEANS.items():
        text = BEAN.format(title=title)
        (repo / ".beans" / f"{bean}--{title.split()[0]}.md").write_text(text, encoding="utf-8")
    return repo


def _kit(repo: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [*BARE, str(TRACKER_CLI), *argv],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _install(repo: Path, dist: Path) -> None:
    probe = subprocess.run(  # nosec B603
        [*BARE, "-c", "import basicly"], capture_output=True, check=False
    )
    assert probe.returncode != 0, "install: the interpreter the kit runs under imports basicly"

    tracker = _from_wheel(
        _wheel("tracker", dist / "tracker"), "tracker", repo, "init", "--import", "beans"
    )
    board = _from_wheel(_wheel("board", dist / "board"), "board", repo, "init")

    assert tracker.returncode == 0, f"install: basicly-tracker init failed: {tracker.stderr}"
    assert "imported 2 record(s) from .beans into .basicly/ledger" in tracker.stdout, (
        f"install: the beans backlog was not imported: {tracker.stdout}"
    )
    assert board.returncode == 0, f"install: basicly-board init failed: {board.stderr}"
    assert (repo / BOARD_SERVER).is_file(), f"install: no board server: {board.stdout}"
    assert not (repo / ".basicly" / "core").exists(), "install: basicly manages this repository"


def _configure(repo: Path) -> None:
    written = _kit(repo, "config", LEDGER, "set", "prefix", "acme")
    shown = _kit(repo, "config", LEDGER)

    assert written.returncode == 0, f"configure: config set refused: {written.stdout}"
    settings = {row["name"]: row for row in json.loads(shown.stdout)["settings"]}
    assert settings["prefix"]["value"] == "acme", f"configure: prefix is {settings['prefix']}"
    assert settings["prefix"]["source"] == "ledger file", f"configure: {settings['prefix']}"


def _use(repo: Path) -> str:
    story = ("--description", "When a customer installs it, I want a record, so I can close it.")
    shaped = (*story, "--acceptance", "- it closes", "--requirements", "- stdlib")
    created = _kit(repo, "create", LEDGER, "--title", "walk the customer path", *shaped)
    assert created.returncode == 0, f"use: create refused: {created.stdout} {created.stderr}"
    record = json.loads(created.stdout)["record"]
    assert record.startswith("acme-"), f"use: {record} is not under the configured prefix"

    claimed = _kit(repo, "claim", LEDGER, record, "--to", "alex")
    closed = _kit(repo, "close", LEDGER, record, "--reason", "walked the customer path")

    assert claimed.returncode == 0, f"use: claim refused: {claimed.stdout} {claimed.stderr}"
    assert closed.returncode == 0, f"use: close refused: {closed.stdout} {closed.stderr}"
    return record


def _served_port(server: subprocess.Popen[bytes], banner: Path) -> int:
    deadline = time.monotonic() + SERVE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        found = BANNER.search(banner.read_text(encoding="utf-8"))
        if found:
            return int(found.group(1))
        if server.poll() is not None:
            pytest.fail(f"see: the board exited {server.returncode}: {banner.read_text('utf-8')}")
        time.sleep(POLL_SECONDS)
    pytest.fail(f"see: the board named no port in {SERVE_TIMEOUT_SECONDS}s")


def _get(port: int, path: str) -> tuple[int, str, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.getheader("Content-Type") or "", response.read().decode()
    finally:
        conn.close()


def _api(port: int, path: str) -> dict[str, Any]:
    status, _, body = _get(port, f"/api/v1{path}")
    assert status == 200, f"see: GET /api/v1{path} answered {status}: {body}"
    return json.loads(body)


def _see(repo: Path, record: str, banner: Path) -> None:
    with banner.open("w", encoding="utf-8") as log:
        server = subprocess.Popen(  # nosec B603
            [*BARE, str(BOARD_SERVER), "serve", LEDGER, "--port", "0"],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
    try:
        port = _served_port(server, banner)
        status, kind, page = _get(port, "/")
        assert status == 200 and kind.startswith("text/html"), f"see: the page answered {status}"
        assert "<title>Tracker</title>" in page, "see: the page is not the tracker board"

        index = _api(port, "")
        opened = _api(port, "/records?status=open")["records"]
        refine = _api(port, "/refine")["records"]
        closed = _api(port, "/records?status=closed")["records"]
        imported = _api(port, "/records/demo-aa01")
    finally:
        server.terminate()
        server.wait(timeout=10)

    assert "GET  /api/v1/records/<id>" in index["endpoints"], f"see: {index}"
    assert {row["record"] for row in opened} == set(BEANS), f"see: open records {opened}"
    assert {row["record"] for row in refine} == set(BEANS), f"see: refine queue {refine}"
    held = [(row["record"], row["fields"]["assignee"]) for row in closed]
    assert held == [(record, "alex")], f"see: closed records {held}"
    assert imported["fields"]["title"] == BEANS["demo-aa01"], f"see: {imported}"


def test_a_customer_installs_configures_uses_and_sees_the_tracker_from_the_built_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for marker in (*writers.AGENT_MARKERS, writers.CLAUDE_CODE_MARKER):
        monkeypatch.delenv(marker, raising=False)
    for name in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        monkeypatch.setenv(name, str(tmp_path / "no-such-gitconfig"))
    repo = _fresh_repository(tmp_path)

    _install(repo, tmp_path / "dist")
    _configure(repo)
    record = _use(repo)
    _see(repo, record, tmp_path / "board.log")
