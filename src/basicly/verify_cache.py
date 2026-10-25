from __future__ import annotations

import hashlib
import json
import math
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .verify_receipt import CachedCheck, implementation_digest

CACHE_PATH = Path(".basicly/usage/verify-cache.json")
_MAX_BYTES = 2 * 1024 * 1024
_MAX_CHECKS = 128


def _identity(root: Path) -> str:
    payload = {
        "version": __version__,
        "repository": str(root.resolve()),
        "recipe": 2,
        "engine": implementation_digest(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _entry(value: object) -> CachedCheck:
    if not isinstance(value, dict) or set(value) - {"components"} != {
        "name",
        "command",
        "mode",
        "inputs",
        "origin",
        "duration_s",
    }:
        raise ValueError("malformed cache entry")
    for name in ("name", "mode", "inputs", "origin"):
        if not isinstance(value[name], str) or not value[name]:
            raise ValueError("invalid cache identity")
    command = value["command"]
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(word, str) and word for word in command)
    ):
        raise ValueError("invalid cache command")
    duration = value["duration_s"]
    if type(duration) not in {float, int} or not math.isfinite(duration) or duration < 0:
        raise ValueError("invalid cache duration")
    raw_components = value.get("components", [])
    if (
        not isinstance(raw_components, list)
        or len(raw_components) > 32
        or any(
            not isinstance(row, list)
            or len(row) != 2
            or not all(isinstance(item, str) for item in row)
            or len(row[1]) != 64
            or any(char not in "0123456789abcdef" for char in row[1])
            for row in raw_components
        )
    ):
        raise ValueError("invalid cache components")
    return CachedCheck(
        value["name"],
        tuple(command),
        value["mode"],
        value["inputs"],
        value["origin"],
        duration,
        tuple((row[0], row[1]) for row in raw_components),
    )


def _read(root: Path) -> tuple[CachedCheck, ...]:
    path = root / CACHE_PATH
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("cache destination leaves this repository")
    with path.open("rb") as stream:
        content = stream.read(_MAX_BYTES + 1)
    if len(content) > _MAX_BYTES:
        raise ValueError("cache exceeds size limit")
    payload = json.loads(content)
    if not isinstance(payload, dict) or payload.get("identity") != _identity(root):
        raise ValueError("cache repository or engine identity changed")
    entries = payload.get("checks")
    if not isinstance(entries, list) or len(entries) > _MAX_CHECKS:
        raise ValueError("invalid cache check inventory")
    parsed = tuple(_entry(entry) for entry in entries)
    if len({(entry.name, entry.command, entry.mode) for entry in parsed}) != len(parsed):
        raise ValueError("duplicate cache checks")
    return parsed


def read(root: Path) -> tuple[tuple[CachedCheck, ...], str]:
    try:
        checks = _read(root)
    except FileNotFoundError:
        return (), "no local verification cache"
    except (OSError, ValueError, TypeError) as exc:
        return (), f"cache unavailable: {type(exc).__name__}"
    else:
        return checks, ""


def _encoded(root: Path, checks: tuple[CachedCheck, ...], overhead_s: float) -> str:
    if not (root / CACHE_PATH).resolve().is_relative_to(root.resolve()):
        raise ValueError("cache destination leaves this repository")
    payload = json.dumps({
        "identity": _identity(root),
        "checks": [asdict(c) for c in checks],
        "overhead_s": overhead_s,
    })
    if len(payload.encode()) > _MAX_BYTES:
        raise ValueError("cache exceeds size limit")
    return payload + "\n"


def write(root: Path, checks: tuple[CachedCheck, ...], *, overhead_s: float) -> None:
    if not checks or len(checks) > _MAX_CHECKS:
        return
    path = root / CACHE_PATH
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        payload = _encoded(root, checks, overhead_s)
        path.parent.mkdir(parents=True, exist_ok=True)
        marker = path.parent / ".gitignore"
        if not marker.exists():
            marker.write_text("*\n", encoding="utf-8")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
    except (OSError, ValueError) as exc:
        print(f"verification cache not saved: {exc}", file=sys.stderr)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as exc:
            print(f"verification cache temporary cleanup failed: {exc}", file=sys.stderr)
