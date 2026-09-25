from __future__ import annotations

import http.client
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO, Any

import pytest

KIT = Path(__file__).parent.parent / ".basicly" / "core" / "kit"
TRIGGER = "When the kit changes under a server, I want a refusal, so I can restart it."
STARTED = re.compile(r"board: http://127\.0\.0\.1:(\d+)/ serves")
DATA_ROUTES = (
    ("GET", "/api/v1"),
    ("GET", "/api/v1/version"),
    ("GET", "/api/v1/stats"),
    ("GET", "/api/v1/records?status=open"),
    ("POST", "/api/v1/records"),
)


def _lines(stream: IO[str], into: queue.Queue[str]) -> None:
    for line in stream:
        into.put(line)


def _call(port: int, method: str, path: str) -> tuple[int, dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    body = json.dumps({"title": "draft"}).encode() if method == "POST" else None
    headers = {"Content-Type": "application/json"} if body else {}
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    text = response.read().decode()
    conn.close()
    return response.status, json.loads(text) if text.startswith("{") else {"text": text}


@pytest.fixture
def kit(tmp_path: Path) -> Path:
    copied = tmp_path / "kit"
    for name in ("board", "tracker"):
        shutil.copytree(KIT / name, copied / name, ignore=shutil.ignore_patterns("__pycache__"))
    return copied


@pytest.fixture
def ledger(kit: Path, tmp_path: Path) -> Path:
    directory = tmp_path / "ledger"
    create = [sys.executable, str(kit / "tracker" / "cli.py"), "create", str(directory)]
    shaped = ["--prefix", "demo", "--title", "seed", "--description", TRIGGER]
    shaped += ["--acceptance", "- a"]
    subprocess.run([*create, *shaped], check=True, capture_output=True, text=True)
    return directory


@pytest.fixture
def served(kit: Path, ledger: Path) -> Iterator[tuple[int, str]]:
    program = str(kit / "board" / "server.py")
    argv = [sys.executable, program, "serve", str(ledger), "--port", "0"]
    process = subprocess.Popen(argv, stderr=subprocess.PIPE, text=True)
    assert process.stderr is not None
    printed: queue.Queue[str] = queue.Queue()
    threading.Thread(target=_lines, args=(process.stderr, printed), daemon=True).start()
    try:
        started = STARTED.search(printed.get(timeout=60))
        assert started, "the server did not print the address it serves"
        yield int(started.group(1)), f"`python3 {program} serve {ledger} --port 0`"
    finally:
        process.terminate()
        process.wait(timeout=30)


def _touch(path: Path) -> None:
    held = path.stat()
    os.utime(path, ns=(held.st_atime_ns, held.st_mtime_ns + 1_000_000_000))


def _rewrite(path: Path) -> None:
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")


@pytest.mark.parametrize("change", [_touch, _rewrite], ids=["touched", "rewritten"])
@pytest.mark.parametrize("changed", ["tracker/templates.py", "board/routes.py"])
def test_a_server_whose_kit_changed_on_disk_refuses_each_data_route_with_the_restart_command(
    served: tuple[int, str], kit: Path, change: Callable[[Path], None], changed: str
) -> None:
    port, restart = served
    before = [_call(port, method, path)[0] for method, path in DATA_ROUTES]

    change(kit / changed)

    answers = [_call(port, method, path) for method, path in DATA_ROUTES]
    assert before == [200, 200, 200, 200, 201]
    for status, report in answers:
        assert status == 503
        assert report["schema"] == "basicly.tracker.api.v1"
        assert "changed on disk since this server started" in report["refused"]
        assert changed in report["refused"]
        assert restart in report["refused"]
    assert _call(port, "GET", "/")[0] == 200


def test_a_server_whose_kit_is_unchanged_keeps_answering(served: tuple[int, str]) -> None:
    port, _ = served

    answers = [_call(port, "GET", "/api/v1/stats")[0] for _ in range(3)]

    assert answers == [200, 200, 200]


PAGE = (KIT / "board" / "web" / "index.html").read_text(encoding="utf-8")


def test_the_page_holds_a_refused_load_in_a_banner_that_stays() -> None:
    banner = re.search(r'<div id="outage"[^>]*>', PAGE)
    load = re.search(r"async function load\(\) \{(.*?)\n\}", PAGE, re.DOTALL)

    assert banner and 'role="alert"' in banner.group(0) and "hidden" in banner.group(0)
    assert load and "outage(error.message)" in load.group(1)
    assert 'outage("")' in load.group(1)
