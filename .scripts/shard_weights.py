from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree  # nosec B405

WEIGHTS = Path("tests") / "shard_weights.json"
CHECK_PREFIX = "check:"
DOWNLOAD = "gh run download <run-id> -p 'shard-junit-*' -D <dir>"


def nodeid(case: ElementTree.Element) -> str | None:
    path = case.get("file")
    name = case.get("name")
    if not path or not name or not path.endswith(".py"):
        return None
    module = path.removesuffix(".py").replace("/", ".")
    classname = case.get("classname") or ""
    inner = classname[len(module) + 1 :] if classname.startswith(f"{module}.") else ""
    return "::".join([path, *([*inner.split(".")] if inner else []), name])


def times(directory: Path) -> dict[str, float]:
    found: dict[str, float] = {}
    for report in sorted(directory.rglob("*.xml")):
        root = ElementTree.parse(report).getroot()  # nosec B314
        for case in root.iter("testcase"):
            key = nodeid(case)
            if key is not None:
                found[key] = found.get(key, 0.0) + float(case.get("time") or 0.0)
    return found


def merge(previous: dict[str, float], measured: dict[str, float], minimum: float) -> dict:
    kept = {key: value for key, value in previous.items() if key.startswith(CHECK_PREFIX)}
    kept |= {key: round(value, 1) for key, value in measured.items() if value >= minimum}
    return dict(sorted(kept.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="merge shard JUnit times into the shard weights")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--out", type=Path, default=WEIGHTS)
    parser.add_argument("--min", type=float, default=0.1)
    args = parser.parse_args(argv)
    measured = times(args.directory)
    if not measured:
        print(
            f"no JUnit test times under {args.directory}; fetch them with {DOWNLOAD}",
            file=sys.stderr,
        )
        return 2
    previous = json.loads(args.out.read_text("utf-8")) if args.out.is_file() else {}
    weights = merge(previous, measured, args.min)
    args.out.write_text(json.dumps(weights, indent=2) + "\n", encoding="utf-8")
    tests = sum(not key.startswith(CHECK_PREFIX) for key in weights)
    print(f"shard weights: {tests} tests from {len(measured)} measured, written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
