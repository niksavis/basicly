"""Per-session harness config overrides, scoped to this process (basicly-jr0l.8).

``loop supervise --runner/--autonomy`` configures one run without touching a
committed file. D10 is the frame: configuring a run is deterministic, so it should
be one command rather than a config edit plus a revert the operator has to
remember.

The alternatives are both worse. Editing ``basicly.toml`` changes behaviour for
every consumer and contradicts that file's own comment — ``default = "manual"`` is
deliberate in this repo so the loop does not auto-dispatch a second agent on top
of the one already driving it. The gitignored local overlay is the intended escape
hatch but it is a persistent file, not a per-session choice, so two concurrent
roots could not differ.

Process-global state is the correct scope here rather than a shortcut: D1 puts one
supervisor process in charge of one session, so "this process" and "this session"
name the same thing. Overriding at the single point where config is read also
means none of the seventeen ``load_*_config`` call sites has to be threaded, and a
missed one would have been a silent half-override.

Its own module because the import graph is acyclic and stays that way: ``config``
imports ``runner`` for the built-in adapters, and ``runner`` imports
``run_record``, so neither of the two modules that need this registry — ``config``
to apply it, ``run_record`` to record it — could host it without a cycle.

Recording matters as much as applying. An override changes what a dispatch *is*
while every committed file stays identical, so an unrecorded one would leave two
genuinely different dispatches behind indistinguishable run records — the
irreproducibility D9 exists to forbid.
"""

from __future__ import annotations

from collections.abc import Mapping

_OVERRIDES: dict[str, dict] = {}


def set_override(section: str, key: str, value: object) -> None:
    """Override one harness config key for this process; no file is written."""
    _OVERRIDES.setdefault(section, {})[key] = value


def overrides_for(section: str) -> Mapping[str, object]:
    """This process's overrides for *section* (empty when none are set)."""
    return _OVERRIDES.get(section, {})


def clear_overrides() -> None:
    """Drop every override. A test seam; no production caller ends a session."""
    _OVERRIDES.clear()


def override_pairs() -> tuple[str, ...]:
    """Active overrides as sorted ``section.key=value`` strings, for provenance.

    Sorted so two run records are diffable: an unordered rendering would make an
    identical pair of dispatches look like they differed.
    """
    return tuple(
        sorted(
            f"{section}.{key}={value}"
            for section, values in _OVERRIDES.items()
            for key, value in values.items()
        )
    )
