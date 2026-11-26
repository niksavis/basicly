from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TypeIs

FORMAT = "basicly.usage-snapshot"
VERSION = 1
PATH_ENV = "BASICLY_USAGE_SNAPSHOT"
NAME = "usage-snapshot.json"
WINDOWS = ("five_hour", "seven_day")
READINGS_MAX = 360
STEP_S = 60
CYCLE_APART_S = 600

Window = dict[str, object]


def path(
    environ: Mapping[str, str], platform: str = sys.platform, home: Path | None = None
) -> Path:
    named = environ.get(PATH_ENV, "").strip()
    if named:
        return Path(named)
    root = home if home is not None else Path.home()
    if platform == "win32":
        local = environ.get("LOCALAPPDATA", "").strip()
        return (Path(local) if local else root / "AppData" / "Local") / "basicly" / NAME
    state = environ.get("XDG_STATE_HOME", "").strip()
    base = Path(state) if state and Path(state).is_absolute() else root / ".local" / "state"
    return base / "basicly" / NAME


def _number(value: object) -> TypeIs[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def incoming(status: object) -> dict[str, tuple[float, int]]:
    limits = status.get("rate_limits") if isinstance(status, dict) else None
    if not isinstance(limits, dict):
        return {}
    found: dict[str, tuple[float, int]] = {}
    for name in WINDOWS:
        window = limits.get(name)
        if not isinstance(window, dict):
            continue
        used, resets = window.get("used_percentage"), window.get("resets_at")
        if _number(used) and _number(resets) and 0 <= used <= 100 and resets > 0:
            found[name] = (float(used), int(resets))
    return found


def _reading(value: object) -> bool:
    return isinstance(value, list) and len(value) == 2 and all(_number(v) for v in value)


def valid_window(window: object) -> bool:
    if not isinstance(window, dict):
        return False
    readings = window.get("readings")
    return (
        all(_number(window.get(k)) for k in ("used_percentage", "resets_at", "read_at"))
        and isinstance(readings, list)
        and bool(readings)
        and all(_reading(r) for r in readings)
    )


def windows(document: object) -> dict[str, Window]:
    if not isinstance(document, dict):
        return {}
    if document.get("format") != FORMAT or document.get("version") != VERSION:
        return {}
    found = document.get("windows")
    if not isinstance(found, dict):
        return {}
    return {name: found[name] for name in WINDOWS if valid_window(found.get(name))}


def merge_window(old: Window | None, used: float, resets: int, now: int) -> Window | None:
    if resets <= now:
        return None
    fresh: Window = {
        "used_percentage": used,
        "resets_at": resets,
        "read_at": now,
        "readings": [[now, used]],
    }
    if old is None or not valid_window(old) or float(old["resets_at"]) <= now:  # type: ignore[arg-type]
        return fresh
    moved = resets - float(old["resets_at"])  # type: ignore[arg-type]
    if moved > CYCLE_APART_S:
        return fresh
    readings = [list(r) for r in old["readings"]]  # type: ignore[union-attr]
    if moved < -CYCLE_APART_S or used < float(old["used_percentage"]) or now < readings[-1][0]:  # type: ignore[arg-type]
        return None
    if len(readings) >= 2 and now - readings[-2][0] < STEP_S:
        readings = readings[:-1]
    readings.append([now, used])
    return {
        "used_percentage": used,
        "resets_at": resets,
        "read_at": now,
        "readings": readings[-READINGS_MAX:],
    }


def merge(
    old: object, found: Mapping[str, tuple[float, int]], now: int, writer: str
) -> dict[str, object] | None:
    kept = windows(old)
    changed = False
    for name, (used, resets) in found.items():
        merged = merge_window(kept.get(name), used, resets, now)
        if merged is not None:
            kept[name] = merged
            changed = True
    if not changed:
        return None
    return {
        "format": FORMAT,
        "version": VERSION,
        "writer": writer,
        "written_at": now,
        "windows": kept,
    }


def load(target: Path) -> object:
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None


def write(target: Path, document: Mapping[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=target.parent, prefix=".usage-snapshot.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(document, out, sort_keys=True)
        Path(name).replace(target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def record(data: bytes, environ: Mapping[str, str], now: int, writer: str) -> bool:
    found = incoming(json.loads(data.decode("utf-8")))
    if not found:
        return False
    target = path(environ)
    merged = merge(load(target), found, now, writer)
    if merged is None:
        return False
    write(target, merged)
    return True
