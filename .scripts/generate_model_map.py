from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from model_map_anchors import Anchors, ResolutionError, load_anchors, require_mapping
from model_map_resolve import UNAVAILABLE, resolve_tiers

from basicly.schema import MODEL_TIERS

API_URL = "https://models.dev/api.json"

USER_AGENT = "basicly-model-map/1 (+https://github.com/niksavis/basicly)"

MAP_SCHEMA_VERSION = 3

MODELS_DIR = Path(".basicly/core/models")
ANCHORS_FILENAME = "anchors.yaml"
MAP_FILENAME = "model-map.json"


CONCEPT = (
    "A capability tier is declared once on an agent source and resolved here to a concrete model "
    "per vendor, then to each surface's own spelling of it — because availability, id spelling "
    "and cost all vary by surface. tier_order is cheapest first. Read "
    "tiers.<tier>.vendors.<vendor>.surfaces.<surface>: when status is 'available' its 'model' is "
    "the value that surface's model field accepts, with that surface's own published cost and "
    "limits beside it; when status is 'unavailable' there is deliberately NO model key, because "
    "substituting another tier's model would be a silent demotion. A 'collapse' entry means the "
    "vendor ships a shorter ladder and two tiers intentionally resolve to one model. When a spawn "
    "pins no vendor, walk tiers.<tier>.vendor_order in order and take the first vendor whose cell "
    "for your surface is available; it lists every vendor exactly once. A tier with no available "
    "vendor on a surface resolves to nothing — never to a neighbouring tier. Plain JSON "
    "on purpose: any harness can consume this file without the tool that generated it."
)

PROVENANCE_NOTE = (
    "models.dev serves api.json as a CDN-cached generated artifact and publishes no git commit "
    "sha: the payload has no commit field and the response has no last-modified header. "
    "payload_sha256 plus etag is therefore the strongest available upstream identity, and no "
    "commit-sha field is claimed. Per-surface upstream_last_updated is models.dev's record date."
)


def fetch_payload(url: str = API_URL, timeout: int = 60) -> tuple[bytes, str | None]:

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 — https URL
        return response.read(), response.headers.get("etag")


def build_provenance(payload: bytes, etag: str | None) -> dict[str, Any]:

    stamp: dict[str, Any] = {
        "source_url": API_URL,
        "fetched_date": datetime.now(UTC).date().isoformat(),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_bytes": len(payload),
    }
    if etag:
        stamp["etag"] = etag
    stamp["note"] = PROVENANCE_NOTE
    return stamp


def build_map(payload: bytes, etag: str | None, anchors: Anchors) -> dict:
    document = require_mapping(json.loads(payload.decode("utf-8")), "models.dev payload")
    return {
        "schema_version": MAP_SCHEMA_VERSION,
        "concept": CONCEPT,
        "generated_by": ".scripts/generate_model_map.py",
        "tier_order": list(MODEL_TIERS),
        "general_model_rule": {
            "require_text_input": anchors.rule.require_text_input,
            "require_text_only_output": anchors.rule.require_text_only_output,
            "require_tool_call": anchors.rule.require_tool_call,
        },
        "vendors": {
            vendor.id: {"name": vendor.name, "surfaces": list(vendor.surfaces)}
            for vendor in anchors.vendors
        },
        "surfaces": {
            surface.id: {
                "consumed_by": surface.consumed_by,
                "accepts": surface.accepts,
                "verified": surface.verified,
            }
            for surface in anchors.surfaces.values()
        },
        "provenance": build_provenance(payload, etag),
        "tiers": resolve_tiers(document, anchors),
    }


def render(document: Mapping[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _flatten(value: object, prefix: str = "") -> Iterator[tuple[str, object]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _flatten(item, f"{prefix}.{key}" if prefix else str(key))
    else:
        yield prefix, value


def _cell(path: str, tiers: Mapping[str, Any]) -> Mapping[str, Any] | None:
    parts = path.split(".")
    if len(parts) < 3 or parts[1] != "vendors":
        return None
    tier = tiers.get(parts[0])
    if not isinstance(tier, Mapping):
        return None
    vendor = require_mapping(tier, "tier").get("vendors", {})
    entry = vendor.get(parts[2]) if isinstance(vendor, Mapping) else None
    return entry if isinstance(entry, Mapping) else None


def _named_id(path: str, tiers: Mapping[str, Any]) -> str:
    entry = _cell(path, tiers)
    if entry is None:
        return ""
    parts = path.split(".")
    if len(parts) >= 5 and parts[3] == "surfaces" and parts[-1] != "model":
        surfaces = entry.get("surfaces")
        served = surfaces.get(parts[4]) if isinstance(surfaces, Mapping) else None
        if isinstance(served, Mapping) and served.get("model"):
            return f" (model '{parts[4]}/{served['model']}')"
    if parts[-1] == "anchor":
        return ""
    return f" (anchor '{parts[2]}/{entry.get('anchor')}')"


def diff_tiers(committed: Mapping[str, Any], resolved: Mapping[str, Any]) -> list[str]:

    old = dict(_flatten(committed))
    new = dict(_flatten(resolved))
    messages: list[str] = []
    for path in sorted(old.keys() | new.keys()):
        if path not in new:
            messages.append(f"{path}: {old[path]!r} is gone upstream{_named_id(path, committed)}")
        elif path not in old:
            messages.append(f"{path}: new upstream value {new[path]!r}{_named_id(path, resolved)}")
        elif old[path] != new[path]:
            messages.append(f"{path}: {old[path]!r} -> {new[path]!r}{_named_id(path, resolved)}")
    return messages


def _load_committed(map_path: Path) -> Mapping[str, Any]:
    if not map_path.is_file():
        raise ResolutionError(
            f"{map_path} does not exist; run the generator without --check to create it"
        )
    return require_mapping(json.loads(map_path.read_text(encoding="utf-8")), str(map_path))


def _report_drift(map_path: Path, committed: Mapping[str, Any], resolved: dict) -> int:
    if committed.get("schema_version") != MAP_SCHEMA_VERSION:
        print(
            f"drift: {map_path} is schema_version {committed.get('schema_version')!r}, this "
            f"generator writes {MAP_SCHEMA_VERSION}; regenerate it",
            file=sys.stderr,
        )
        return 1

    messages = diff_tiers(
        require_mapping(committed.get("tiers"), f"{map_path} 'tiers'"), resolved["tiers"]
    )
    if not messages:
        print(f"ok: {map_path} matches models.dev (provenance is not compared)")
        return 0

    print(
        f"drift: {map_path} disagrees with models.dev in {len(messages)} place(s):", file=sys.stderr
    )
    for message in messages:
        print(f"  {message}", file=sys.stderr)
    print(
        "The committed map was NOT modified. Review the change above, then re-run without "
        "--check to accept it as a reviewable diff.",
        file=sys.stderr,
    )
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate and drift-check the tier by vendor by surface model map."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare the committed map against upstream and fail on drift without writing it",
    )
    parser.add_argument(
        "--payload",
        type=Path,
        help="read a captured api.json from this file instead of fetching (offline review); "
        "provenance still records the upstream URL, since a capture is a copy of it",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / MODELS_DIR,
        help=f"directory holding {ANCHORS_FILENAME} and {MAP_FILENAME}",
    )
    return parser


def _summary(resolved: Mapping[str, Any]) -> str:
    cells = [
        served
        for tier in resolved["tiers"].values()
        for vendor in tier["vendors"].values()
        for served in vendor["surfaces"].values()
    ]
    unavailable = sum(1 for served in cells if served["status"] == UNAVAILABLE)
    return (
        f"{len(resolved['tiers'])} tiers, {len(resolved['vendors'])} vendors, "
        f"{len(cells)} cells, {unavailable} unavailable"
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    models_dir: Path = args.models_dir
    map_path = models_dir / MAP_FILENAME

    try:
        anchors = load_anchors(models_dir / ANCHORS_FILENAME)
        payload, etag = (args.payload.read_bytes(), None) if args.payload else fetch_payload()
        committed = _load_committed(map_path) if args.check else None
        resolved = build_map(payload, etag, anchors)
    except (ResolutionError, OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if committed is not None:
        return _report_drift(map_path, committed, resolved)

    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(render(resolved), encoding="utf-8", newline="\n")
    print(f"wrote {map_path} ({_summary(resolved)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
