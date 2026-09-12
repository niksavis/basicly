from __future__ import annotations

from collections.abc import Mapping

_OVERRIDES: dict[str, dict] = {}


def set_override(section: str, key: str, value: object) -> None:
    _OVERRIDES.setdefault(section, {})[key] = value


def overrides_for(section: str) -> Mapping[str, object]:
    return _OVERRIDES.get(section, {})


def clear_overrides() -> None:
    _OVERRIDES.clear()


def override_pairs() -> tuple[str, ...]:

    return tuple(
        sorted(
            f"{section}.{key}={value}"
            for section, values in _OVERRIDES.items()
            for key, value in values.items()
        )
    )
