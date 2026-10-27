from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

GROUP_FIELDS = ("family", "model", "effort", "arm", "case")
MEASUREMENTS = ("duration_s", "tokens", "cost")
OUTCOMES = frozenset({"passed", "failed", "timed_out", "infrastructure_error", "interrupted"})
ACCEPTANCE_OUTCOMES = frozenset({"passed", "failed", "infrastructure_error"})


def validate(row: Mapping[str, Any]) -> None:
    for key in ("id", *GROUP_FIELDS):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"trial requires a nonempty {key}")
    if row.get("outcome") not in OUTCOMES:
        raise ValueError(f"trial {row['id']} has an invalid outcome")
    acceptance = row.get("acceptance")
    if acceptance is not None and (
        not isinstance(acceptance, Mapping) or acceptance.get("outcome") not in ACCEPTANCE_OUTCOMES
    ):
        raise ValueError(f"trial {row['id']} has invalid acceptance")
    for key in MEASUREMENTS:
        value = row.get(key)
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or (key == "tokens" and not isinstance(value, int))
        ):
            raise ValueError(f"trial {row['id']} has invalid {key}")


def measurements(rows: list[Mapping[str, Any]], key: str) -> dict[str, Any]:
    values = [row[key] for row in rows if row.get(key) is not None]
    return {
        "observed": len(values),
        "missing": len(rows) - len(values),
        "known_sum": sum(values) if values else None,
        "median": statistics.median(values) if values else None,
        "observed_max": max(values) if values else None,
    }


def summarize(trials: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for row in trials:
        validate(row)
        identifier = row["id"]
        if identifier in seen:
            raise ValueError(f"duplicate trial id: {identifier}")
        seen.add(identifier)
        grouped[tuple(row[key] for key in GROUP_FIELDS)].append(row)
    if not seen:
        raise ValueError("no trials to summarize")
    summaries = []
    for group, rows in sorted(grouped.items()):
        outcomes = Counter(row["outcome"] for row in rows)
        acceptance = Counter(
            row["acceptance"]["outcome"] for row in rows if row.get("acceptance") is not None
        )
        passed = outcomes["passed"]
        metrics = {key: measurements(rows, key) for key in MEASUREMENTS}
        summary: dict[str, Any] = {
            **dict(zip(GROUP_FIELDS, group, strict=True)),
            "attempts": len(rows),
            "passed": passed,
            "outcomes": dict(sorted(outcomes.items())),
            "acceptance_outcomes": dict(sorted(acceptance.items())),
            "acceptance_missing": len(rows) - acceptance.total(),
            "attempt_ids": [row["id"] for row in rows],
            **metrics,
        }
        for key in ("cost", "tokens"):
            metric = metrics[key]
            summary[f"{key}_per_success"] = (
                metric["known_sum"] / passed if passed and not metric["missing"] else None
            )
        summaries.append(summary)
    return summaries


def read_trials(paths: list[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: trial must be an object")
            rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize observed harness trials without filling unknown metrics."
    )
    parser.add_argument("trials", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        summary = summarize(read_trials(args.trials))
    except (OSError, ValueError) as error:
        parser.exit(2, f"harness-trial-results: {error}\n")
    print(json.dumps({"schema_version": 1, "groups": summary}, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
