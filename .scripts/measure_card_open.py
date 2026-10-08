from __future__ import annotations

import argparse
import http.client
import re
import subprocess  # nosec B404 - starts this repository's own server.py
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import IO
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER = REPO_ROOT / ".basicly" / "core" / "kit" / "board" / "server.py"
API = "/api/v1"
STATUSES = ("open", "in_progress", "blocked", "deferred")
PAGE_LISTS = (
    API,
    API + "/ready?limit=5000",
    API + "/blocked",
    API + "/refine",
    *(f"{API}/records?status={status}" for status in STATUSES),
)
TRIALS = 5
BUDGET_S = 1.0
TIMEOUT_S = 120.0
SERVING = re.compile(r"^board: http://[^/]+:(\d+)/ serves ")


def _get(port: int, path: str) -> float:
    started = time.monotonic()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT_S)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        response.read()
    finally:
        conn.close()
    if response.status >= 400:
        raise SystemExit(f"measure_card_open: GET {path} answered {response.status}")
    return time.monotonic() - started


def _card_paths(record: str) -> tuple[str, str]:
    path = f"{API}/records/{quote(record, safe='')}"
    return path, path + "/dor"


def _trial(port: int, record: str, pool: ThreadPoolExecutor) -> float:
    lists = [pool.submit(_get, port, path) for path in PAGE_LISTS]
    started = time.monotonic()
    card = [pool.submit(_get, port, path) for path in _card_paths(record)]
    for future in card:
        future.result()
    elapsed = time.monotonic() - started
    for future in lists:
        future.result()
    return elapsed


def _port_of(stream: IO[str]) -> int:
    told = []
    for line in stream:
        told.append(line)
        found = SERVING.match(line)
        if found:
            threading.Thread(target=stream.read, daemon=True).start()
            return int(found.group(1))
    raise SystemExit("measure_card_open: the server stopped before it served:\n" + "".join(told))


@contextmanager
def _served(ledger: Path) -> Iterator[int]:
    argv = [sys.executable, str(SERVER), "serve", str(ledger), "--port", "0"]
    child = subprocess.Popen(  # nosec B603 - argv is fixed, no shell
        argv, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True
    )
    try:
        if child.stderr is None:
            raise SystemExit("measure_card_open: the server's stderr is not a pipe")
        yield _port_of(child.stderr)
    finally:
        child.terminate()
        child.wait(timeout=TIMEOUT_S)


def measure(ledger: Path, record: str, trials: int = TRIALS) -> list[float]:
    with _served(ledger) as port, ThreadPoolExecutor(max_workers=len(PAGE_LISTS) + 2) as pool:
        for path in (*PAGE_LISTS, *_card_paths(record)):
            _get(port, path)
        return [_trial(port, record, pool) for _ in range(trials)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Time a card open on the kit board server while the page's lists load."
    )
    parser.add_argument("ledger", help="the ledger directory to serve")
    parser.add_argument("record", help="the record id whose card to open")
    args = parser.parse_args(argv)
    ledger = Path(args.ledger)
    if not ledger.is_dir():
        parser.error(f"{ledger} is not a ledger directory")
    seconds = measure(ledger, args.record)
    worst = max(seconds)
    shown = ", ".join(f"{one:.3f}" for one in seconds)
    if worst < BUDGET_S:
        print(f"card open under {BUDGET_S} s: worst {worst:.3f} s of {len(seconds)} ({shown})")
        return 0
    print(f"card open took {worst:.3f} s, over {BUDGET_S} s: {len(seconds)} trials ({shown})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
