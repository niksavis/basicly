#!/usr/bin/env python3


from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGETS_DIR = REPO_ROOT / ".basicly/core/targets"

VENDOR_LIMIT = {
    "codex": (
        32768,
        "project_doc_max_bytes, 32 KiB default, shared with every ancestor AGENTS.md; "
        "Codex truncates past it silently (openai/codex#13386, open)",
    ),
    "claude": (
        4 * 1024 * 1024,
        "Claude Code loads a CLAUDE.md up to 4 MiB and skips a larger one; the vendor "
        "states its own budget in lines, not bytes",
    ),
}

RETENTION_NOTE = (
    "Measured 2026-09-12 by .scripts/retention_eval.py on claude, n=1 per point: "
    "152 lines 95% retained with a flat curve, 266 lines 92% flat, 700 lines 82% with "
    "the second half 14pp below the first, 1667 lines 79% with the second half 23pp "
    "below. The line cap keeps a file inside the flat zone."
)


def _unit(target: dict[str, object]) -> str:
    return str(target.get("max_size_unit", "characters"))


def _measure(text: str, unit: str) -> int:
    return len(text.encode("utf-8")) if unit == "bytes" else len(text)


def surfaces() -> list[tuple[str, Path, dict[str, object]]]:
    found: list[tuple[str, Path, dict[str, object]]] = []
    for path in sorted(TARGETS_DIR.glob("*.yaml")):
        target = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(target, dict) or not target.get("enabled", False):
            continue
        name = str(target.get("name") or path.stem)
        for output in (target.get("outputs") or {}).values():
            surface = output.get("path") if isinstance(output, dict) else None
            if surface:
                found.append((name, REPO_ROOT / surface, target))
    return found


def check() -> list[str]:
    failures: list[str] = []
    found = surfaces()
    if not found:
        return ["no enabled target declares an always-on output; the gate would pass for free"]

    for name, path, target in found:
        if not path.is_file():
            failures.append(f"{path.name} ({name}): declared by the target but not projected")
            continue
        text = path.read_text(encoding="utf-8")
        unit = _unit(target)
        size = _measure(text, unit)
        lines = len(text.splitlines())
        size_cap = target.get("max_size_warning")
        line_cap = target.get("max_lines_warning")
        vendor, why = VENDOR_LIMIT.get(name, (None, ""))

        if isinstance(line_cap, int) and line_cap and lines > line_cap:
            failures.append(
                f"{path.name} ({name}): {lines} lines over the {line_cap}-line cap. "
                f"{RETENTION_NOTE}"
            )
        if isinstance(size_cap, int) and size_cap and size > size_cap:
            note = f" The vendor limit is {vendor} {unit}: {why}." if vendor else ""
            failures.append(
                f"{path.name} ({name}): {size} {unit} over the {size_cap}-{unit} cap.{note}"
            )
        if vendor is not None and isinstance(size_cap, int) and size_cap > vendor:
            failures.append(
                f"{path.name} ({name}): the declared cap {size_cap} exceeds the vendor limit "
                f"{vendor} {unit} ({why}); a cap looser than the limit refuses nothing"
            )
    return failures


def main() -> int:
    failures = check()
    for failure in failures:
        print(f"always-on-size: {failure}")
    if failures:
        return 1
    for name, path, target in surfaces():
        text = path.read_text(encoding="utf-8")
        unit = _unit(target)
        print(
            f"always-on-size: {path.name} ({name}) {_measure(text, unit)}/"
            f"{target.get('max_size_warning')} {unit}, "
            f"{len(text.splitlines())}/{target.get('max_lines_warning')} lines"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
