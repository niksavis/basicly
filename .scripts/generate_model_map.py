"""Generate and drift-check the tier x vendor x surface model map from models.dev.

A catalog source declares a model *tier* (``schema.MODEL_TIERS``) because a
provider model id is portable to exactly one surface: Anthropic spells one model
``claude-haiku-4-5`` and GitHub Copilot spells the same model
``claude-haiku-4.5``. ``.basicly/core/models/anchors.yaml`` names one anchor model
per (tier, vendor); this script resolves each anchor to every surface that vendor
is served on and writes ``.basicly/core/models/model-map.json``.

Three axes, because all three change the answer:

* **tier** — the portable capability level a source declares.
* **vendor** — who makes the model. Only Anthropic publishes a genuine fourth
  class, so the other vendors declare an explicit ``collapse`` of maximum onto
  high rather than silently repeating a row.
* **surface** — where the id gets written. Availability *and cost* vary by
  surface, not just by vendor: measured 2026-07-31, ``gpt-5.6-luna`` is 0.2/1.2
  USD per MTok on ``openai`` and 1/6 on ``github-copilot``. A single per-vendor
  cost would be wrong.

A tier may legitimately have no model on a surface — github-copilot serves exactly
one Moonshot model and no ``gemini-3.6-flash``. That is recorded as
``status: unavailable`` with a reason and **no** ``model`` key, so a consumer
reading ``["model"]`` fails loudly. Substituting a different tier's model would be
the silent demotion basicly-izda exists to prevent, and never happens here.

Two constraints shape the whole design:

* **The fetch runs at authoring and check time only, never in the dispatch
  path.** Nothing that dispatches an agent reads models.dev, so determinism holds
  and the harness gains no runtime network dependency. There is deliberately no
  ``[[verify.checks]]`` entry: the drift check needs the network, and a gate that
  needs the network must not run on every commit. Run ``--check`` by hand or from
  a scheduled job; the committed map's *shape* is gated offline by
  ``tests/test_model_map.py``.
* **The map is committed and reviewed as a diff, and the drift check reports
  rather than auto-applies.** models.dev is community-contributed, so a bad
  upstream edit must surface as a red check, never as a silent change to which
  model runs someone's code. ``--check`` never writes.

models.dev has no field linking a provider's serving id to the underlying model
(there is no ``base_model``: it is absent from all 5911 records and from the
record schema). The join is exact ``name`` equality, corroborated by ``family``.
The ``" (latest)"`` suffix is part of the join key and must not be stripped:
Anthropic serves both ``claude-haiku-4-5`` as "Claude Haiku 4.5 (latest)" and
``claude-haiku-4-5-20251001`` as "Claude Haiku 4.5", so stripping it makes the low
anchor ambiguous.

Usage::

    uv run python .scripts/generate_model_map.py            # fetch and write
    uv run python .scripts/generate_model_map.py --check     # fetch and compare
    uv run python .scripts/generate_model_map.py --payload captured.json --check
"""

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

# models.dev sits behind a CDN that answers the default `Python-urllib/3.x` agent
# with 403 (measured 2026-07-31: 403 with no agent header, 200 with this one), so
# the request identifies itself. Contactable rather than a browser impersonation.
USER_AGENT = "basicly-model-map/1 (+https://github.com/niksavis/basicly)"

# Bump when the map's shape changes in a way a foreign consumer must notice. The
# map is a standalone artifact, so its version is its own, not basicly's.
MAP_SCHEMA_VERSION = 3

MODELS_DIR = Path(".basicly/core/models")
ANCHORS_FILENAME = "anchors.yaml"
MAP_FILENAME = "model-map.json"


# Explains the mechanism to a reader who has only the JSON file, so the idea
# travels with the data (see .basicly/core/models/README.md for the long form).
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

# models.dev publishes api.json as a CDN-cached generated artifact. Verified
# 2026-07-31: the payload carries no commit field (the top level is provider ids
# only) and the response carries no last-modified header, so there is no upstream
# git commit sha to stamp. The payload digest plus the CDN etag is the strongest
# available upstream identity; per-surface upstream_last_updated is models.dev's
# own per-record date.
PROVENANCE_NOTE = (
    "models.dev serves api.json as a CDN-cached generated artifact and publishes no git commit "
    "sha: the payload has no commit field and the response has no last-modified header. "
    "payload_sha256 plus etag is therefore the strongest available upstream identity, and no "
    "commit-sha field is claimed. Per-surface upstream_last_updated is models.dev's record date."
)


# --- fetching and provenance -------------------------------------------------


def fetch_payload(url: str = API_URL, timeout: int = 60) -> tuple[bytes, str | None]:
    """Fetch the models.dev catalog, returning the raw bytes and the CDN etag.

    The raw bytes are what gets digested for provenance, so the stamp identifies
    the exact document parsed rather than a re-serialization of it.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 — https URL
        return response.read(), response.headers.get("etag")


def build_provenance(payload: bytes, etag: str | None) -> dict[str, Any]:
    """Stamp the fetched document: source, date, digest, size, and etag.

    ``source_url`` is always the upstream URL, never a local ``--payload`` path: a
    captured payload is a copy of that document, and a committed artifact must
    carry no machine-specific path.
    """
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


# --- resolution --------------------------------------------------------------


def build_map(payload: bytes, etag: str | None, anchors: Anchors) -> dict:
    """Build the whole committed artifact from a fetched payload."""
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
    """Serialize the map the way it is committed: 2-space JSON, LF, trailing newline."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


# --- drift -------------------------------------------------------------------


def _flatten(value: object, prefix: str = "") -> Iterator[tuple[str, object]]:
    """Yield ``dotted.path -> leaf`` pairs so any field change is comparable."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _flatten(item, f"{prefix}.{key}" if prefix else str(key))
    else:
        yield prefix, value


def _cell(path: str, tiers: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The (tier, vendor) entry a drift path belongs to, when it names one."""
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
    """The model id a drift path belongs to, so every message names an id."""
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
    """Report every difference between the committed and freshly resolved tiers.

    Only the ``tiers`` section is compared. Provenance changes on every fetch —
    other providers edit the shared upstream document constantly — so treating a
    new digest as drift would make the check fire daily and mean nothing.
    """
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


# --- entry point -------------------------------------------------------------


def _load_committed(map_path: Path) -> Mapping[str, Any]:
    """Read the committed map, raising a pointed error when it is absent."""
    if not map_path.is_file():
        raise ResolutionError(
            f"{map_path} does not exist; run the generator without --check to create it"
        )
    return require_mapping(json.loads(map_path.read_text(encoding="utf-8")), str(map_path))


def _report_drift(map_path: Path, committed: Mapping[str, Any], resolved: dict) -> int:
    """Compare and report; never writes, so the committed map cannot mutate."""
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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
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
    """A one-line count of what was written, including the explicit gaps."""
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
    """Generate the map, or drift-check it, returning a process exit code."""
    args = _build_parser().parse_args(argv)
    models_dir: Path = args.models_dir
    map_path = models_dir / MAP_FILENAME

    try:
        anchors = load_anchors(models_dir / ANCHORS_FILENAME)
        payload, etag = (args.payload.read_bytes(), None) if args.payload else fetch_payload()
        committed = _load_committed(map_path) if args.check else None
        resolved = build_map(payload, etag, anchors)
    # PEP 758 drops the parentheses only when there is no `as` clause, and this
    # needs the exception object for the one-line diagnostic.
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
