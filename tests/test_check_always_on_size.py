from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).parent.parent
GATE = REPO / ".scripts/check_always_on_size.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_always_on_size", GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gate():
    return _module()


def _targets() -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for path in sorted((REPO / ".basicly/core/targets").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        out[str(data.get("name") or path.stem)] = data
    return out


def test_the_committed_tree_passes(gate) -> None:
    assert gate.check() == []


def test_every_enabled_target_is_measured(gate) -> None:
    measured = {name for name, _, _ in gate.surfaces()}

    assert measured == {"claude", "codex", "copilot"}


def test_codex_is_measured_in_bytes_not_characters() -> None:
    assert _targets()["codex"]["max_size_unit"] == "bytes"


def test_the_byte_and_character_counts_actually_differ() -> None:
    text = (REPO / "AGENTS.md").read_text(encoding="utf-8")

    assert len(text.encode("utf-8")) > len(text), (
        "if these were equal the unit choice would be untestable here"
    )


def test_no_declared_cap_is_looser_than_its_vendor_limit(gate) -> None:
    targets = _targets()
    for name, (limit, _why) in gate.VENDOR_LIMIT.items():
        assert targets[name]["max_size_warning"] <= limit, name


def test_a_size_breach_is_reported(gate, monkeypatch: pytest.MonkeyPatch) -> None:
    real = gate.surfaces

    def shrunk() -> list:
        return [(name, path, {**target, "max_size_warning": 1}) for name, path, target in real()]

    monkeypatch.setattr(gate, "surfaces", shrunk)
    failures = gate.check()

    assert len(failures) == 3
    assert all("over the 1-" in line for line in failures)


def test_a_line_breach_is_reported(gate, monkeypatch: pytest.MonkeyPatch) -> None:
    real = gate.surfaces

    def shrunk() -> list:
        return [(name, path, {**target, "max_lines_warning": 1}) for name, path, target in real()]

    monkeypatch.setattr(gate, "surfaces", shrunk)
    failures = gate.check()

    assert all("over the 1-line cap" in line for line in failures)


def test_a_line_breach_carries_the_measurement_that_set_the_cap(
    gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = gate.surfaces
    monkeypatch.setattr(
        gate,
        "surfaces",
        lambda: [(n, p, {**t, "max_lines_warning": 1}) for n, p, t in real()],
    )

    assert "retention_eval.py" in gate.check()[0]


def test_a_cap_looser_than_the_vendor_limit_is_refused(
    gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = gate.surfaces

    def loosened() -> list:
        return [
            (name, path, {**target, "max_size_warning": 10**9} if name == "codex" else target)
            for name, path, target in real()
        ]

    monkeypatch.setattr(gate, "surfaces", loosened)

    assert any("exceeds the vendor limit 32768" in line for line in gate.check())


def test_a_missing_projection_is_reported(gate, monkeypatch: pytest.MonkeyPatch) -> None:
    real = gate.surfaces
    monkeypatch.setattr(
        gate,
        "surfaces",
        lambda: [(n, p.with_name("absent.md"), t) for n, p, t in real()],
    )

    assert all("not projected" in line for line in gate.check())


def test_no_surfaces_at_all_is_a_failure_not_a_free_pass(
    gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "surfaces", list)

    assert gate.check() == [
        "no enabled target declares an always-on output; the gate would pass for free"
    ]
