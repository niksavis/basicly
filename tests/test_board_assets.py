from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from basicly import board_assets, board_render

REPO_ROOT = Path(__file__).parent.parent
VENDOR = REPO_ROOT / ".basicly" / "core" / "templates" / "board" / board_assets.DIRNAME


def test_the_vendored_stylesheet_is_the_pinned_release() -> None:
    header = (VENDOR / board_assets.STYLESHEET).read_text(encoding="utf-8")[:200]
    found = re.search(r"Bootstrap\s+v(\d+\.\d+\.\d+)", header)
    assert found is not None, header
    assert found.group(1) == board_assets.BOOTSTRAP_VERSION

    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["devDependencies"]["bootstrap"] == board_assets.BOOTSTRAP_VERSION

    provenance = (VENDOR / "PROVENANCE.txt").read_text(encoding="utf-8")
    assert f"version   {board_assets.BOOTSTRAP_VERSION}" in provenance
    assert "MIT" in (VENDOR / "LICENSE").read_text(encoding="utf-8")


def test_every_asset_in_the_table_is_a_file_in_the_catalog() -> None:
    for name in board_assets.ASSETS:
        assert board_assets.source(board_render.root(), name).is_file(), name
    assert board_assets.read(board_render.root(), "LICENSE") is None
    assert board_assets.read(board_render.root(), "../board_page.html.j2") is None


def test_a_page_at_the_root_and_a_record_page_both_resolve_to_the_written_file(
    tmp_path: Path,
) -> None:
    written = board_assets.write_beside(board_render.root(), tmp_path)

    assert [path.name for path in written] == list(board_assets.ASSETS)
    root_href = board_assets.href(board_assets.STYLESHEET)
    nested_href = board_assets.href(board_assets.STYLESHEET, nested=True)
    assert (tmp_path / root_href).is_file()
    assert (tmp_path / "record" / nested_href).resolve() == (tmp_path / root_href).resolve()
    assert (tmp_path / root_href).read_bytes() == (VENDOR / board_assets.STYLESHEET).read_bytes()


@pytest.mark.parametrize("name", list(board_assets.ASSETS))
def test_read_answers_the_bytes_and_the_type_the_server_sends(name: str) -> None:
    held = board_assets.read(board_render.root(), name)
    assert held is not None
    body, content_type = held
    assert body == (VENDOR / name).read_bytes()
    assert content_type == board_assets.ASSETS[name]
