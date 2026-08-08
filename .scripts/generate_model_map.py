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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from basicly.schema import MODEL_TIERS

API_URL = "https://models.dev/api.json"

# models.dev sits behind a CDN that answers the default `Python-urllib/3.x` agent
# with 403 (measured 2026-07-31: 403 with no agent header, 200 with this one), so
# the request identifies itself. Contactable rather than a browser impersonation.
USER_AGENT = "basicly-model-map/1 (+https://github.com/niksavis/basicly)"

# Bump when the map's shape changes in a way a foreign consumer must notice. The
# map is a standalone artifact, so its version is its own, not basicly's.
MAP_SCHEMA_VERSION = 2

# The anchors source format this generator reads.
ANCHORS_SCHEMA_VERSION = 2

MODELS_DIR = Path(".basicly/core/models")
ANCHORS_FILENAME = "anchors.yaml"
MAP_FILENAME = "model-map.json"

AVAILABLE = "available"
UNAVAILABLE = "unavailable"

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
    "vendor ships a shorter ladder and two tiers intentionally resolve to one model. Plain JSON "
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


class ResolutionError(RuntimeError):
    """An anchor could not be resolved, or resolved to something unusable."""


@dataclass(frozen=True)
class GeneralModelRule:
    """The capability floor an anchor must clear to be usable as a tier.

    Without it a sweep happily picks an image, TTS, embedding, realtime or video
    model: Google publishes 41 records of which 21 are not general text models.
    """

    require_text_input: bool
    require_text_only_output: bool
    require_tool_call: bool

    def failures(self, record: Mapping[str, Any]) -> list[str]:
        """Name every requirement this record fails (empty when it is usable)."""
        modalities = record.get("modalities")
        modalities = modalities if isinstance(modalities, Mapping) else {}
        inputs = raw if isinstance(raw := modalities.get("input"), list) else []
        outputs = raw if isinstance(raw := modalities.get("output"), list) else []
        failed: list[str] = []
        if self.require_text_input and "text" not in inputs:
            failed.append(f"takes no text input (modalities.input={inputs})")
        if self.require_text_only_output and outputs != ["text"]:
            failed.append(f"does not emit text only (modalities.output={outputs})")
        if self.require_tool_call and record.get("tool_call") is not True:
            failed.append(f"does not support tool calls (tool_call={record.get('tool_call')!r})")
        return failed


@dataclass(frozen=True)
class Surface:
    """One place a resolved model can be written. The key is a models.dev provider."""

    id: str
    consumed_by: str
    accepts: str
    verified: str


@dataclass(frozen=True)
class Vendor:
    """One model publisher: its anchors, the surfaces serving it, its collapses."""

    id: str
    name: str
    surfaces: tuple[str, ...]
    tiers: Mapping[str, str]
    collapse: Mapping[str, str]
    collapse_reason: str


@dataclass(frozen=True)
class Anchors:
    """The whole reviewed anchor source."""

    surfaces: Mapping[str, Surface]
    vendors: tuple[Vendor, ...]
    rule: GeneralModelRule


def _require_mapping(value: object, where: str) -> Mapping[str, Any]:
    """Return ``value`` as a mapping or raise naming ``where``."""
    if not isinstance(value, Mapping):
        raise ResolutionError(f"{where} must be a mapping, got {type(value).__name__}")
    return value


def _require_number(record: Mapping[str, Any], key: str, where: str) -> float | int:
    """Return a numeric field or raise naming the record it came from."""
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ResolutionError(f"{where} has a non-numeric '{key}': {value!r}")
    return value


def _require_text(record: Mapping[str, Any], key: str, where: str) -> str:
    """Return a non-empty string field or raise naming ``where``."""
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ResolutionError(f"{where} must have a non-empty '{key}'")
    return value.strip()


# --- the anchor source -------------------------------------------------------


def load_anchors(path: Path) -> Anchors:
    """Load and validate the anchor source.

    Every vendor's tier keys must be exactly ``schema.MODEL_TIERS`` — the
    vocabulary has one definition and this file may not extend or shrink it.
    """
    raw = _require_mapping(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))

    version = raw.get("schema_version")
    if version != ANCHORS_SCHEMA_VERSION:
        raise ResolutionError(
            f"{path}: unsupported schema_version {version!r} (expected {ANCHORS_SCHEMA_VERSION})"
        )

    surfaces = _load_surfaces(raw.get("surfaces"), path)
    rule = _load_rule(raw.get("general_model_rule"), path)
    vendors = _load_vendors(raw.get("vendors"), surfaces, path)
    return Anchors(surfaces=surfaces, vendors=vendors, rule=rule)


def _load_surfaces(value: object, path: Path) -> Mapping[str, Surface]:
    """Validate the surface table, keyed by models.dev provider id."""
    table = _require_mapping(value, f"{path}: 'surfaces'")
    if not table:
        raise ResolutionError(f"{path}: 'surfaces' must not be empty")
    surfaces: dict[str, Surface] = {}
    for name, entry in table.items():
        where = f"{path}: surfaces['{name}']"
        record = _require_mapping(entry, where)
        surfaces[str(name)] = Surface(
            id=str(name),
            consumed_by=_require_text(record, "consumed_by", where),
            accepts=_require_text(record, "accepts", where),
            verified=_require_text(record, "verified", where),
        )
    return surfaces


def _load_rule(value: object, path: Path) -> GeneralModelRule:
    """Validate the general-model rule, which must be stated explicitly."""
    record = _require_mapping(value, f"{path}: 'general_model_rule'")
    flags = {}
    for key in ("require_text_input", "require_text_only_output", "require_tool_call"):
        flag = record.get(key)
        if not isinstance(flag, bool):
            raise ResolutionError(f"{path}: general_model_rule['{key}'] must be true or false")
        flags[key] = flag
    return GeneralModelRule(**flags)


def _load_vendors(value: object, surfaces: Mapping[str, Surface], path: Path) -> tuple[Vendor, ...]:
    """Validate the vendor list, its tier coverage, and its declared collapses."""
    if not isinstance(value, list) or not value:
        raise ResolutionError(f"{path}: 'vendors' must be a non-empty list")
    vendors = [_load_vendor(entry, index, surfaces, path) for index, entry in enumerate(value)]
    ids = [vendor.id for vendor in vendors]
    if len(set(ids)) != len(ids):
        raise ResolutionError(f"{path}: duplicate vendor id in {ids}")
    return tuple(vendors)


def _load_vendor(entry: object, index: int, surfaces: Mapping[str, Surface], path: Path) -> Vendor:
    """Validate one vendor entry."""
    where = f"{path}: vendors[{index}]"
    record = _require_mapping(entry, where)
    vendor_id = _require_text(record, "id", where)
    where = f"{path}: vendor '{vendor_id}'"

    names = record.get("surfaces")
    if not isinstance(names, list) or not names:
        raise ResolutionError(f"{where}: 'surfaces' must be a non-empty list")
    unknown = [name for name in names if name not in surfaces]
    if unknown:
        raise ResolutionError(f"{where}: undeclared surfaces {unknown}")
    if len(set(names)) != len(names):
        raise ResolutionError(f"{where}: duplicate surface in {names}")
    # The vendor's own provider must be listed, so its native cost is always the
    # baseline a broker's markup is read against.
    if vendor_id not in names:
        raise ResolutionError(f"{where}: 'surfaces' must include the vendor's own id")

    tiers = _require_mapping(record.get("tiers"), f"{where}: 'tiers'")
    # The set, not the order: the map's tier order comes from MODEL_TIERS either
    # way, so enforcing key order here would add a failure mode with no safety
    # value. A missing or invented tier is named so the fix needs no guessing.
    missing = [tier for tier in MODEL_TIERS if tier not in tiers]
    unknown = sorted(set(tiers) - set(MODEL_TIERS))
    if missing or unknown:
        raise ResolutionError(
            f"{where}: 'tiers' must declare exactly {list(MODEL_TIERS)} — "
            f"missing {missing}, unknown {unknown}"
        )
    for tier in tiers:
        _require_text(tiers, tier, f"{where}: tier '{tier}'")

    collapse = _load_collapse(record, tiers, where)
    return Vendor(
        id=vendor_id,
        name=_require_text(record, "name", where),
        surfaces=tuple(str(name) for name in names),
        # Re-keyed in MODEL_TIERS order so the generated map's row order is the
        # vocabulary's, whatever order the source happens to list them in.
        tiers={tier: str(tiers[tier]).strip() for tier in MODEL_TIERS},
        collapse=collapse,
        collapse_reason=str(record.get("collapse_reason", "")).strip(),
    )


def _load_collapse(
    record: Mapping[str, Any], tiers: Mapping[str, Any], where: str
) -> Mapping[str, str]:
    """Validate a declared tier collapse against the ids it claims to describe.

    A collapse is a claim about the data; cross-checking it here is what stops the
    declaration and the ids drifting apart.
    """
    raw = record.get("collapse")
    if raw is None:
        return {}
    table = _require_mapping(raw, f"{where}: 'collapse'")
    collapse: dict[str, str] = {}
    for tier, target in table.items():
        if tier not in MODEL_TIERS or target not in MODEL_TIERS:
            raise ResolutionError(
                f"{where}: collapse '{tier}' -> '{target}' names a tier outside {list(MODEL_TIERS)}"
            )
        if tier == target:
            raise ResolutionError(f"{where}: collapse '{tier}' cannot target itself")
        if tiers[tier] != tiers[target]:
            raise ResolutionError(
                f"{where}: collapse claims '{tier}' is '{target}' but they name different "
                f"models ({tiers[tier]!r} vs {tiers[target]!r})"
            )
        collapse[str(tier)] = str(target)
    if collapse and not str(record.get("collapse_reason", "")).strip():
        raise ResolutionError(f"{where}: a declared collapse needs a 'collapse_reason'")
    return collapse


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


def _provider_models(payload: Mapping[str, Any], provider: str) -> Mapping[str, Any]:
    """Return one provider's model records or raise naming the provider."""
    entry = payload.get(provider)
    if entry is None:
        raise ResolutionError(f"provider '{provider}' is not in the models.dev payload")
    return _require_mapping(
        _require_mapping(entry, f"provider '{provider}'").get("models"),
        f"provider '{provider}' 'models'",
    )


def _check_general(record: Mapping[str, Any], rule: GeneralModelRule, where: str) -> None:
    """Refuse a model that is not a general text tool-calling model."""
    failures = rule.failures(record)
    if failures:
        raise ResolutionError(f"{where} is not usable as a tier: it {'; and it '.join(failures)}")


def _match_by_name(
    models: Mapping[str, Any], model_name: str, provider: str, where: str
) -> str | None:
    """The single id ``provider`` serves ``model_name`` under, or None.

    None means genuinely unavailable. Two matches is a guess, so it raises rather
    than picking one.
    """
    matches = sorted(
        model_id for model_id, record in models.items() if record.get("name") == model_name
    )
    if not matches:
        return None
    if len(matches) > 1:
        raise ResolutionError(
            f"{where}: provider '{provider}' serves {len(matches)} models named "
            f"{model_name!r}: {matches}"
        )
    return matches[0]


def _serving_entry(record: Mapping[str, Any], model_id: str, provider: str) -> dict[str, Any]:
    """The serving data one surface publishes for one model."""
    where = f"'{provider}/{model_id}'"
    cost = _require_mapping(record.get("cost"), f"{where} 'cost'")
    limit = _require_mapping(record.get("limit"), f"{where} 'limit'")

    limits: dict[str, Any] = {"context": _require_number(limit, "context", f"{where} 'limit'")}
    # Only some providers publish a separate input cap, and not for every model
    # (github-copilot's claude-sonnet-5 has none), so it is optional. Where it is
    # absent, `context` governs the input side.
    if "input" in limit:
        limits["input"] = _require_number(limit, "input", f"{where} 'limit'")
    limits["output"] = _require_number(limit, "output", f"{where} 'limit'")

    return {
        "status": AVAILABLE,
        "model": model_id,
        "cost_usd_per_mtok": {
            "input": _require_number(cost, "input", f"{where} 'cost'"),
            "output": _require_number(cost, "output", f"{where} 'cost'"),
        },
        "limit_tokens": limits,
        "upstream_last_updated": record.get("last_updated"),
    }


def _resolve_surface(
    payload: Mapping[str, Any],
    surface: str,
    model_name: str,
    rule: GeneralModelRule,
    where: str,
) -> dict[str, Any]:
    """Resolve one (model, surface) pair to a serving entry or an explicit gap."""
    models = _provider_models(payload, surface)
    model_id = _match_by_name(models, model_name, surface, where)
    if model_id is None:
        return {
            "status": UNAVAILABLE,
            "reason": f"provider '{surface}' serves no model named {model_name!r}",
        }
    record = _require_mapping(models[model_id], f"'{surface}/{model_id}'")
    _check_general(record, rule, f"{where}: '{surface}/{model_id}'")
    return _serving_entry(record, model_id, surface)


def _resolve_vendor_tier(
    payload: Mapping[str, Any], vendor: Vendor, tier: str, rule: GeneralModelRule
) -> dict[str, Any]:
    """Resolve one (tier, vendor) anchor across every surface serving that vendor."""
    where = f"tier '{tier}' vendor '{vendor.id}'"
    anchor_id = vendor.tiers[tier]
    anchor_record = _provider_models(payload, vendor.id).get(anchor_id)
    if anchor_record is None:
        raise ResolutionError(
            f"{where}: anchor '{vendor.id}/{anchor_id}' no longer resolves upstream "
            f"(no such model id)"
        )
    anchor_record = _require_mapping(anchor_record, f"'{vendor.id}/{anchor_id}'")
    _check_general(anchor_record, rule, f"{where}: anchor '{vendor.id}/{anchor_id}'")

    model_name = _require_text(anchor_record, "name", f"{where}: anchor '{anchor_id}'")
    entry: dict[str, Any] = {
        "anchor": anchor_id,
        "model_name": model_name,
        "family": anchor_record.get("family"),
        "reasoning": anchor_record.get("reasoning"),
    }
    if tier in vendor.collapse:
        entry["collapse"] = {
            "same_model_as_tier": vendor.collapse[tier],
            "reason": vendor.collapse_reason,
        }
    entry["surfaces"] = {
        surface: _resolve_surface(payload, surface, model_name, rule, where)
        for surface in vendor.surfaces
    }
    return entry


def resolve_tiers(payload: Mapping[str, Any], anchors: Anchors) -> dict[str, Any]:
    """Resolve every (tier, vendor, surface) cell."""
    return {
        tier: {
            "vendors": {
                vendor.id: _resolve_vendor_tier(payload, vendor, tier, anchors.rule)
                for vendor in anchors.vendors
            }
        }
        for tier in MODEL_TIERS
    }


def build_map(payload: bytes, etag: str | None, anchors: Anchors) -> dict:
    """Build the whole committed artifact from a fetched payload."""
    document = _require_mapping(json.loads(payload.decode("utf-8")), "models.dev payload")
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
    vendor = _require_mapping(tier, "tier").get("vendors", {})
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
    return _require_mapping(json.loads(map_path.read_text(encoding="utf-8")), str(map_path))


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
        _require_mapping(committed.get("tiers"), f"{map_path} 'tiers'"), resolved["tiers"]
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
