from __future__ import annotations

from pathlib import Path

from basicly import kit_load

KEPT_HELPER = "kit/demo/helper.py"


def _bundled(root: Path) -> Path:

    kit = root / "bundled" / "kit" / "demo"
    kit.mkdir(parents=True)
    (kit / "entry.py").write_text("import helper\n\nVALUE = helper.VALUE\n", encoding="utf-8")
    (kit / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root / "bundled"


def _core_keeping(root: Path, helper_source: str) -> Path:

    core = root / "core"
    (core / "kit" / "demo").mkdir(parents=True)
    (core / KEPT_HELPER).write_text(helper_source, encoding="utf-8")
    return core


def test_a_kept_module_that_breaks_its_kit_is_reported_with_the_load_error(
    tmp_path: Path,
) -> None:
    core = _core_keeping(tmp_path, "OTHER = 1\n")

    found = kit_load.unloadable_kits(_bundled(tmp_path), core, [KEPT_HELPER])

    assert [(kit.name, kit.kept) for kit in found] == [("demo", (KEPT_HELPER,))]
    assert found[0].error == "AttributeError: module 'helper' has no attribute 'VALUE'"


def test_a_kept_module_that_still_loads_with_its_kit_is_not_reported(tmp_path: Path) -> None:
    core = _core_keeping(tmp_path, "VALUE = 2\nLOCAL = True\n")

    assert kit_load.unloadable_kits(_bundled(tmp_path), core, [KEPT_HELPER]) == []


def test_a_kept_file_outside_a_kit_module_is_never_loaded(tmp_path: Path) -> None:
    core = _core_keeping(tmp_path, "raise SystemExit(3)\n")
    (core / "hooks").mkdir()
    (core / "hooks" / "broken.py").write_text("raise SystemExit(3)\n", encoding="utf-8")

    kept = ["hooks/broken.py", "kit/demo/SPEC.md"]

    assert kit_load.unloadable_kits(_bundled(tmp_path), core, kept) == []


def test_the_refusal_names_the_kit_the_error_and_every_kept_file() -> None:
    kit = kit_load.UnloadableKit("demo", (KEPT_HELPER,), "AttributeError: boom")

    text = kit_load.refusal(".basicly/core", [kit])

    assert "would mix kit modules that cannot load together" in text
    assert "  kit/demo: AttributeError: boom" in text
    assert f"    kept: {KEPT_HELPER}" in text
    assert "--force" in text
