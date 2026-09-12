from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from basicly import board_icons, board_regions, board_render, board_schema
from tests.board_offline import assert_offline
from tests.test_board_wall import FIXTURES, REPO_ROOT, STAMPED

TEMPLATES = REPO_ROOT / ".basicly" / "core" / "templates" / "board"
ICONS_DIR = TEMPLATES / board_icons.DIRNAME
_SVG = "{http://www.w3.org/2000/svg}"

_BAND_STATES = ("calm", "waiting", "stuck", "withheld", "absent")


def _vendored(icon: str) -> tuple[str, bool]:
    root = ElementTree.parse(ICONS_DIR / f"{icon}.svg").getroot()
    paths = root.findall(f"{_SVG}path")
    assert len(paths) == 1, f"{icon} holds {len(paths)} paths"
    return paths[0].attrib["d"], paths[0].get("fill-rule") == "evenodd"


def _document(state: str) -> dict[str, Any]:
    doc = json.loads((FIXTURES / "dense-v1.json").read_text(encoding="utf-8"))
    if state == "calm":
        doc["asks"] = []
    elif state == "stuck":
        doc["asks"][0]["waiting_s"] = board_regions.BAND_ALARM_AFTER_S * 2
    elif state == "withheld":
        doc["asks"] = [{"wait_id": 5}]
    elif state == "absent":
        del doc["asks"]
    return doc


def _content_codepoints(page: str) -> list[str]:

    code = re.sub(r"/\*.*?\*/", "", page, flags=re.DOTALL)
    return [
        value
        for value in re.findall(r'content:\s*"([^"]*)"', code)
        if not value.isascii() or "\\" in value
    ]


def _band(page: str) -> str:
    found = re.search(r'<section class="region band framed.*?</section>', page, re.DOTALL)
    assert found is not None, "the page drew no band"
    return found.group(0)


def _page(doc: dict[str, Any], *, after_s: float = 8.0) -> str:
    now = STAMPED + timedelta(seconds=after_s)
    return board_render.page(
        doc, board_schema.verdict(REPO_ROOT, doc), now=now, templates_dir=TEMPLATES
    )


@pytest.mark.parametrize("mark", sorted(board_icons.ICONS))
def test_a_mark_carries_the_vendored_path_and_its_winding_rule(mark: str) -> None:

    icon = board_icons.ICONS[mark]
    path, even_odd = _vendored(icon)
    read = board_icons.read_mark(ICONS_DIR, mark)
    assert (read.path, read.even_odd) == (path, even_odd)
    assert board_icons.DIGESTS[icon] == board_icons.digest(
        board_icons.Mark(path=path, even_odd=even_odd)
    )


def test_the_even_odd_rule_discriminates_between_two_vendored_icons() -> None:
    assert board_icons.read_mark(ICONS_DIR, "chain").even_odd is True
    assert board_icons.read_mark(ICONS_DIR, "checkpoint").even_odd is False


def test_the_vendored_directory_holds_only_the_icons_the_roster_names() -> None:

    assert {path.stem for path in ICONS_DIR.glob("*.svg")} == set(board_icons.ICONS.values())
    assert (ICONS_DIR / "LICENSE").is_file()
    assert board_icons.VERSION in (ICONS_DIR / "PROVENANCE.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("state", _BAND_STATES)
def test_the_band_draws_its_own_state_mark_and_no_other(state: str) -> None:

    band = _band(_page(_document(state)))
    marks = board_icons.marks(TEMPLATES)
    assert marks[state].path in band
    others = [other for other in _BAND_STATES if other != state and marks[other].path in band]
    assert not others, f"the {state} band also drew {others}"


def test_the_loop_and_the_queue_draw_their_marks_from_the_vendored_files() -> None:
    page = _page(_document("waiting"))
    for mark in ("checkpoint", "chain"):
        path, _ = _vendored(board_icons.ICONS[mark])
        assert page.count(f'd="{path}"') >= 1, f"the page drew no {mark}"


def test_the_chain_hop_carries_its_winding_rule_into_the_page() -> None:
    page = _page(_document("waiting"))
    path, _ = _vendored(board_icons.ICONS["chain"])
    assert f'd="{path}" fill-rule="evenodd"' in page


@pytest.mark.parametrize("state", _BAND_STATES)
def test_the_page_references_no_origin_and_no_css_declaration_emits_a_codepoint(
    state: str,
) -> None:

    page = _page(_document(state))
    assert_offline(page)
    assert _content_codepoints(page + '\na::before { content: "\\2192"; }\n') == ["\\2192"]
    assert _content_codepoints(page) == []


def test_a_vendored_file_holding_two_paths_is_refused(tmp_path: Path) -> None:
    icon = board_icons.ICONS["checkpoint"]
    one, _ = _vendored(icon)
    (tmp_path / f"{icon}.svg").write_text(
        f'<svg viewBox="0 0 16 16"><path d="{one}"/><path d="M0 0h1v1z"/></svg>',
        encoding="utf-8",
    )
    with pytest.raises(board_icons.IconError, match="holds 2 paths"):
        board_icons.read_mark(tmp_path, "checkpoint")


def test_a_vendored_file_on_another_grid_is_refused(tmp_path: Path) -> None:
    icon = board_icons.ICONS["checkpoint"]
    one, _ = _vendored(icon)
    (tmp_path / f"{icon}.svg").write_text(
        f'<svg viewBox="0 0 24 24"><path d="{one}"/></svg>', encoding="utf-8"
    )
    with pytest.raises(board_icons.IconError, match="16x16 grid"):
        board_icons.read_mark(tmp_path, "checkpoint")


def test_a_missing_vendored_file_names_the_path_it_looked_for(tmp_path: Path) -> None:
    with pytest.raises(board_icons.IconError, match=re.escape(str(tmp_path))):
        board_icons.marks(tmp_path)
