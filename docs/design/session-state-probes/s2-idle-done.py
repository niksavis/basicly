from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import sys
import time

SOURCE = "orch-probe-s2"
WANTED = ("done", "idle")


def herdr(*args: str) -> subprocess.CompletedProcess[str]:
    binary = os.environ.get("HERDR_BIN_PATH") or "herdr"
    return subprocess.run(  # noqa: S603 # nosec B603 - herdr with literal argv, no shell
        [binary, *args], capture_output=True, text=True, check=False, timeout=2
    )


def pane(pane_id: str) -> dict[str, object]:
    return json.loads(herdr("pane", "get", pane_id).stdout)["result"]["pane"]


def label(pane_id: str, status: str | None) -> None:
    seq = str(time.time_ns())
    if status is None:
        herdr(
            "pane",
            "report-metadata",
            pane_id,
            "--source",
            SOURCE,
            "--clear-state-labels",
            "--seq",
            seq,
        )
        return
    text = f"{status}=probe s2"
    herdr(
        "pane",
        "report-metadata",
        pane_id,
        "--source",
        SOURCE,
        "--state-label",
        text,
        "--ttl-ms",
        "5000",
        "--seq",
        seq,
    )


def main() -> int:
    pane_id = os.environ.get("HERDR_PANE_ID", "")
    if not pane_id:
        print("refused: run inside a herdr pane", file=sys.stderr)
        return 2
    limit = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
    results: dict[str, object] = {}
    end = time.monotonic() + limit
    while time.monotonic() < end and len(results) < len(WANTED):
        status = str(pane(pane_id).get("agent_status"))
        if status in WANTED and status not in results:
            label(pane_id, status)
            after = []
            for _ in range(3):
                time.sleep(0.3)
                info = pane(pane_id)
                after.append((info.get("agent_status"), info.get("state_labels")))
            label(pane_id, None)
            cleared = pane(pane_id).get("state_labels")
            results[status] = {"before": status, "after": after, "cleared": cleared}
        time.sleep(1)
    print(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
