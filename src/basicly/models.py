"""Resolve a declared model tier into the concrete model a dispatch may pin.

The seam between the two halves basicly-kjc5.58 and basicly-kjc5.61 landed: a
catalog source declares a portable *model tier* (`schema.MODEL_TIERS`) and
``.basicly/core/models/model-map.json`` holds the tier x vendor x surface
resolution. Nothing here reaches the network — the map is committed data, and
the fetch that produced it happens at authoring time only (see
``.basicly/core/models/README.md``), so the dispatch path stays deterministic
and dependency-free.

Three rules this module exists to keep:

- **An unavailable cell refuses; it never substitutes.** The map records
  "unavailable" with no ``model`` key on purpose, so a lookup raises instead of
  handing back some other tier's model. That silent demotion is what
  basicly-izda exists to prevent, and a refusal at dispatch is cheaper than
  discovering mid-run that the wrong model did the work.
- **Surface is not cosmetic.** The same model is ``claude-haiku-4-5`` to
  Anthropic and ``claude-haiku-4.5`` to GitHub Copilot, and copilot rejects the
  hyphenated form outright, so the family a dispatch runs on picks the spelling.
- **A family that cannot express a model is recorded, not pretended.** The
  handoff runner has no argv to pin a model onto; a tier aimed at it is reported
  as unhonoured rather than as satisfied.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .catalog import bundled_catalog_root
from .schema import MODEL_TIERS

# Where the map lives inside a catalog root, whichever root that is.
MODELS_DIRNAME = "models"
MAP_FILENAME = "model-map.json"

# The catalog root as `basicly install` materializes it into a consumer repo.
# Preferred over the bundled copy so a consumer that regenerated or reviewed its
# own map gets the one it committed, not the one the wheel happens to carry.
LOCAL_CATALOG_DIR = Path(".basicly") / "core"

# How each runner family spells a model, and whose models it serves by default.
#
# `surface` is the map surface whose spelling the family's `--model` flag
# accepts, and is a property of the family rather than a choice. `vendor` is the
# default only: the two single-vendor CLIs can serve nobody else, while copilot
# is a multi-vendor surface (its own store shows anthropic ids on 28 of 28 local
# sessions, which is why anthropic is its default) and is overridable per agent
# with `[[runner.agents]] vendor`.
FAMILY_MODEL_SURFACES: dict[str, tuple[str, str]] = {
    "claude": ("anthropic", "anthropic"),
    "codex": ("openai", "openai"),
    "copilot": ("github-copilot", "anthropic"),
}


class ModelMapError(RuntimeError):
    """The map itself is missing or unreadable — distinct from an absent cell."""


class ModelUnavailableError(LookupError):
    """No model exists for this (tier, vendor, surface). Never a substitution."""


class ModelResolutionError(RuntimeError):
    """A tier was declared but no model could be pinned, so the dispatch refuses.

    Raised *before* anything is spawned. Refusing costs one clear error; guessing
    costs a whole run done by the wrong model, discovered afterwards from
    telemetry if at all.
    """


def map_path(repo_root: Path | None = None) -> Path:
    """Where the model map is read from, consumer copy first.

    A repo that carries its own ``.basicly/core/models/model-map.json`` wins,
    because that is the file its own gates reviewed; the copy inside the wheel is
    the fallback for a consumer that has not run `basicly install` yet.
    """
    if repo_root is not None:
        local = repo_root / LOCAL_CATALOG_DIR / MODELS_DIRNAME / MAP_FILENAME
        if local.is_file():
            return local
    return bundled_catalog_root() / MODELS_DIRNAME / MAP_FILENAME


def load_map(repo_root: Path | None = None) -> dict:
    """The parsed model map for *repo_root*, consumer copy first."""
    return load_map_from(map_path(repo_root))


def load_map_from(path: Path) -> dict:
    """The parsed model map at *path*.

    Raises :class:`ModelMapError` rather than returning an empty mapping when the
    file is absent or malformed: a dispatch that cannot read the map must refuse,
    and an empty map would look exactly like "every tier is unavailable" while
    actually meaning "the data is broken".
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ModelMapError(f"cannot read the model map at '{path}': {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelMapError(f"the model map at '{path}' is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("tiers"), dict):
        raise ModelMapError(f"the model map at '{path}' has no 'tiers' section")
    return parsed


def model_for(
    tier: str,
    vendor: str,
    surface: str,
    *,
    mapping: dict | None = None,
    repo_root: Path | None = None,
) -> str:
    """The model id to pin for *tier* from *vendor* as spelled on *surface*.

    Raises :class:`ModelUnavailableError` for an unknown tier/vendor/surface and for a
    cell the map marks unavailable — the reason is carried through verbatim so the
    refusal names the real cause rather than "not found".
    """
    if tier not in MODEL_TIERS:
        raise ModelUnavailableError(f"unknown model tier {tier!r}; known: {list(MODEL_TIERS)}")
    data = mapping if mapping is not None else load_map(repo_root)
    tiers = data.get("tiers", {})
    entry = tiers.get(tier)
    if not isinstance(entry, dict):
        raise ModelUnavailableError(f"the model map carries no tier {tier!r}")
    vendors = entry.get("vendors")
    vendor_entry = vendors.get(vendor) if isinstance(vendors, dict) else None
    if not isinstance(vendor_entry, dict):
        raise ModelUnavailableError(f"the model map carries no vendor {vendor!r} for tier {tier!r}")
    surfaces = vendor_entry.get("surfaces")
    cell = surfaces.get(surface) if isinstance(surfaces, dict) else None
    if not isinstance(cell, dict):
        raise ModelUnavailableError(
            f"the model map carries no surface {surface!r} for {vendor!r} tier {tier!r}"
        )
    model = cell.get("model")
    if cell.get("status") != "available" or not isinstance(model, str) or not model:
        reason = cell.get("reason") or "the map marks this cell unavailable"
        raise ModelUnavailableError(f"{vendor} {tier} is unavailable on {surface}: {reason}")
    return model


@dataclass(frozen=True)
class ModelResolution:
    """How one dispatch's model was decided, including when it could not be.

    Carried on the run record so the telemetry says *why* a model ran, not just
    which one: a tier that fell back to the session model and a tier that was
    honoured are different facts, and basicly-jr0l.21 cannot calibrate a
    per-model forecast against a value whose provenance is unknown.
    """

    model: str | None = None
    tier: str | None = None
    # Which input decided it — an agent's explicit `model` pin, its configured
    # `tier`, or the family default. Recorded verbatim in the run record.
    source: str | None = None
    # False when a tier was asked for and the family cannot pin a model at all,
    # so the dispatch ran on whatever model the session already had.
    honoured: bool = True
    # Why it was not honoured; None whenever it was.
    note: str | None = None


# --- Observed-model identity --------------------------------------------------
#
# The adapter reports the model it actually used in a different spelling from the
# one we pin, so equality is the wrong test (measured 2026-07-31, recorded on
# basicly-kjc5.59): claude's result event keys `modelUsage` by the DATED id
# `claude-haiku-4-5-20251001` while the pin — and the map's anthropic surface —
# say `claude-haiku-4-5`, and a bare alias like `haiku` is also a legal pin. A
# literal comparison would therefore report a mismatch on every healthy claude
# dispatch and make the mismatch signal worthless.
_ALIAS_RE = re.compile(r"[^a-z0-9]+")


def _normalize(model: str) -> str:
    """Fold a model id to a comparable form: lowercase, separators collapsed.

    Turns copilot's dotted `claude-haiku-4.5` and Anthropic's hyphenated
    `claude-haiku-4-5` into the same string, which is the whole point — they are
    one model under two surface spellings.
    """
    return _ALIAS_RE.sub("-", model.strip().lower()).strip("-")


def same_model(pinned: str, observed: str) -> bool:
    """Whether *observed* is the model *pinned* asked for.

    True on an exact match, on a dated build of the pinned id
    (`claude-haiku-4-5` vs `claude-haiku-4-5-20251001`), and on a bare capability
    alias the CLI resolves for us (`haiku`). Deliberately conservative in the
    other direction: an unrelated id is a mismatch, because the mismatch record
    is the only signal that an injected tier silently did not take.
    """
    left, right = _normalize(pinned), _normalize(observed)
    if left == right:
        return True
    if right.startswith(f"{left}-") or left.startswith(f"{right}-"):
        return True
    # A bare alias carries no version at all, so containment is the only join
    # available; requiring a segment boundary keeps `haiku` from matching an
    # unrelated id that merely embeds the letters.
    if "-" not in left and left.isalpha():
        return left in right.split("-")
    return False
