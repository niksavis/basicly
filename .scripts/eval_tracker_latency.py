from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import shutil
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from basicly import checkout, tracker, tracker_query

ROOT = Path(__file__).resolve().parents[1]


def _inputs(ledger: Path) -> dict[str, str]:
    result = {}
    for path in sorted(ledger.iterdir()):
        if path.name == "snapshot.jsonl" or path.suffix not in {".json", ".jsonl"}:
            continue
        if path.is_symlink() or path.name.casefold().startswith(".env"):
            raise ValueError("unsupported ledger input")
        if path.is_file():
            result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not result:
        raise ValueError("latency measurement requires an existing ledger")
    return result


def _request(port: int, method: str, path: str, body: dict | None = None) -> tuple[Any, float]:
    started = time.perf_counter()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(
            method, path, body=json.dumps(body) if body is not None else None, headers=headers
        )
        response = connection.getresponse()
        content = response.read(8 * 1024 * 1024 + 1)
        if response.status not in {200, 201} or len(content) > 8 * 1024 * 1024:
            raise ValueError(f"tracker request {method} {path} failed: HTTP {response.status}")
        result = json.loads(content)
    finally:
        connection.close()
    return result, time.perf_counter() - started


def _distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "samples": len(values),
        "values_s": values,
        "median_s": statistics.median(values),
        "p95_s": ordered[math.ceil(len(values) * 0.95) - 1],
        "max_s": max(values),
    }


def _exercise(port: int, samples: int) -> dict[str, Any]:
    stats, cold = _request(port, "GET", "/api/v1/stats")
    listing, _ = _request(port, "GET", "/api/v1/records?limit=1")
    records = listing.get("records") if isinstance(listing, dict) else None
    if not records:
        raise ValueError("latency measurement requires a nonempty record set")
    record = records[0]["record"]
    path = "/api/v1/records/" + quote(record, safe="")
    targets = {"stats": "/api/v1/stats", "record": path, "ready": "/api/v1/ready?limit=10"}
    warm = {}
    for name, target in targets.items():
        warm[name] = _distribution([_request(port, "GET", target)[1] for _ in range(samples)])
    previous, _ = _request(port, "GET", "/api/v1/version")
    title = "latency fixture " + uuid4().hex
    created, write_s = _request(
        port, "POST", "/api/v1/records", {"title": title, "prefix": "probe"}
    )
    acknowledged = time.perf_counter()
    current, _ = _request(port, "GET", "/api/v1/version")
    shown, _ = _request(port, "GET", "/api/v1/records/" + quote(created["record"], safe=""))
    visible = shown.get("fields", {}).get("title") == title
    if current["version"] == previous["version"] or not visible:
        raise ValueError("tracker did not expose the acknowledged write")
    return {
        "records": stats["records"],
        "tombstoned": stats.get("tombstoned"),
        "cold_stats_s": cold,
        "warm": warm,
        "write_s": write_s,
        "freshness": {
            "version_changed": True,
            "record_visible": visible,
            "ack_to_visible_s": time.perf_counter() - acknowledged,
            "scope": "HTTP version and record reads; frontend polling and rendering excluded",
        },
        "budgets": {
            "status": "proposed",
            "derivation": "twice observed warm p95, rounded up to milliseconds",
            "warm_api_s": {
                name: math.ceil(row["p95_s"] * 2000) / 1000 for name, row in warm.items()
            },
        },
    }


_ISOLATED_MEASURE = """
import json
import runpy
import sys
from pathlib import Path
module = runpy.run_path(sys.argv[1])
result = module["_measure"](Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]))
print(json.dumps(result, allow_nan=False))
"""


def measure(repo: Path, ledger: Path, samples: int) -> dict[str, Any]:
    if type(samples) is not int or not 1 <= samples <= 1000:
        raise ValueError("samples must be an integer from 1 through 1000")
    done = checkout.run(
        [
            sys.executable,
            "-c",
            _ISOLATED_MEASURE,
            str(Path(__file__).resolve()),
            str(repo.resolve()),
            str(ledger.resolve()),
            str(samples),
        ],
        cwd=repo,
    )
    return json.loads(done.stdout)


def _measure(repo: Path, ledger: Path, samples: int) -> dict[str, Any]:
    if type(samples) is not int or not 1 <= samples <= 1000:
        raise ValueError("samples must be an integer from 1 through 1000")
    before = _inputs(ledger)
    with tempfile.TemporaryDirectory(prefix="basicly-tracker-latency-") as temporary:
        copied = Path(temporary) / "ledger"
        copied.mkdir()
        for name in before:
            shutil.copyfile(ledger / name, copied / name)
        if _inputs(ledger) != before or _inputs(copied) != before:
            raise ValueError("source ledger changed during snapshot copy")
        started = time.perf_counter()
        server = tracker_query.board_kit(repo)
        served = server.make_server(copied, "127.0.0.1", 0)
        worker = threading.Thread(target=served.serve_forever, daemon=True)
        worker.start()
        startup = time.perf_counter() - started
        try:
            result = _exercise(served.server_address[1], samples)
            implementation = {
                path.relative_to(repo.resolve()).as_posix(): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in server.loaded_kit_files()
            }
        finally:
            served.shutdown()
            served.server_close()
            worker.join(5)
        if worker.is_alive():
            raise ValueError("latency fixture server did not stop")
    if _inputs(ledger) != before:
        raise ValueError("source ledger changed during measurement")
    return {
        "schema_version": 1,
        **result,
        "server_startup_s": startup,
        "source_sha256": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        "source_files": len(before),
        "api_python_sha256": implementation,
        "cold_definition": (
            "first stats request against a copied ledger without derived snapshot; "
            "OS caches retained"
        ),
        "source_unchanged": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure tracker HTTP latency on a private ledger copy."
    )
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if any(part.casefold().startswith(".env") for part in args.out.resolve().parts):
            parser.error("protected output path")
        result = measure(args.repo.resolve(), tracker.ledger_dir(args.repo), args.samples)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(2, f"eval-tracker-latency: {error}\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("records", "cold_stats_s", "warm", "freshness", "budgets")
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
