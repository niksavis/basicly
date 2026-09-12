from __future__ import annotations

import os
import re
import time
from pathlib import Path

from basicly import board_assets, board_render, board_serve
from tests.test_board_render import render
from tests.test_board_wall import REPO_ROOT

SITE = REPO_ROOT / "site" / "index.html"

TEMPLATES = board_render.root()
PAGES = sorted(path for path in TEMPLATES.glob("board_*.html.j2"))
THEME = TEMPLATES / "board_theme.css.j2"

ADOPTED = frozenset({"btn", "card", "table", "table-sm"})

_CLASS_ATTR = re.compile(r'class="([^"]*)"')
_SELECTOR_CLASS = re.compile(r"\.([a-zA-Z][\w-]*)")
_BUTTON = re.compile(r"<button\b[^>]*>")
_JINJA = re.compile(r"\{[%#].*?[%#]\}", re.DOTALL)
_DEFINED = re.compile(r"^\s*(--[a-z-]+):", re.MULTILINE)
_USED = re.compile(r"var\((--[a-z-]+)\)")
_BRIDGE = re.compile(r"^\s*(--bs-[a-z-]+):\s*([^;]+);", re.MULTILINE)
_SITE_HEX = re.compile(r"^\s*--[a-z-]+:\s*#([0-9a-fA-F]{6})\s*;", re.MULTILINE)
_TRIPLE = re.compile(r"(\d{1,3}),\s*(\d{1,3}),\s*(\d{1,3})")


def _classes(template: str) -> set[str]:
    used = set()
    for attribute in _CLASS_ATTR.findall(template):
        used.update(token for token in attribute.split() if "{" not in token)
    style = template.split("<style>", 1)[1].split("</style>", 1)[0]
    used.update(_SELECTOR_CLASS.findall(_JINJA.sub("", style)))
    return used


def test_every_page_links_the_stylesheet_and_includes_the_one_theme() -> None:
    assert len(PAGES) == 4, [path.name for path in PAGES]
    for path in PAGES:
        template = path.read_text(encoding="utf-8")
        assert template.count('<link rel="stylesheet" href="{{ stylesheet }}" />') == 1, path.name
        assert template.count('{% include "board_theme.css.j2" %}') == 1, path.name
        assert ":root" not in template, f"{path.name} carries its own palette"
        assert 'data-bs-theme="dark"' in template, path.name
    theme = THEME.read_text(encoding="utf-8")
    assert theme.count(":root {") == 1


def test_every_control_a_reader_presses_is_the_boards_bootstrap_button() -> None:
    found = 0
    for path in PAGES:
        for tag in _BUTTON.findall(path.read_text(encoding="utf-8")):
            found += 1
            assert 'class="btn btn-board' in tag, f"{path.name}: {tag}"
    assert found > 0, "no page draws a control, so the rule asserts nothing"
    theme = THEME.read_text(encoding="utf-8")
    assert ".btn-board {" in theme
    assert "--bs-btn-focus-shadow-rgb" in theme
    assert "--bs-btn-disabled-color" in theme


def test_a_board_class_bootstrap_also_defines_is_adopted_or_renamed() -> None:
    vendored = (TEMPLATES / board_assets.DIRNAME / board_assets.STYLESHEET).read_text(
        encoding="utf-8"
    )
    bootstrap = set(_SELECTOR_CLASS.findall(vendored))
    for path in PAGES:
        collisions = _classes(path.read_text(encoding="utf-8")) & bootstrap
        assert collisions <= ADOPTED, f"{path.name} collides on {sorted(collisions - ADOPTED)}"
    assert bootstrap >= ADOPTED, "an adopted name is not bootstrap's, so the gate names nothing"


def test_staleness_is_dated_off_the_newest_template_not_the_wall_alone(tmp_path: Path) -> None:
    wall = tmp_path / "board_page.html.j2"
    theme = tmp_path / "board_theme.css.j2"
    wall.write_text("wall", encoding="utf-8")
    theme.write_text("theme", encoding="utf-8")
    old, new = time.time() - 3600, time.time() + 3600
    os.utime(wall, (old, old))
    os.utime(theme, (new, new))

    assert board_serve.newest_template_mtime(tmp_path) == new
    assert board_serve.newest_template_mtime(tmp_path / "absent") is None
    (tmp_path / "empty").mkdir()
    assert board_serve.newest_template_mtime(tmp_path / "empty") is None


def test_the_page_uses_only_the_palette_the_site_already_ships() -> None:

    page = render("wall-v1.json")
    site_css = SITE.read_text(encoding="utf-8")
    site = set(_DEFINED.findall(site_css))
    site_rgb = {
        tuple(int(hexa[i : i + 2], 16) for i in (0, 2, 4)) for hexa in _SITE_HEX.findall(site_css)
    }
    defined = set(_DEFINED.findall(page))
    bridged = {name for name in defined if name.startswith("--bs-")}
    invented = defined - bridged - site
    assert not invented, f"invented custom properties: {sorted(invented)}"
    assert set(_USED.findall(page)) <= defined
    assert bridged, "no bootstrap property is bridged, so its components draw in its colours"
    for name, value in _BRIDGE.findall(page):
        assert "#" not in value, f"{name} carries a literal colour: {value}"
        for used in _USED.findall(value):
            assert used in site, f"{name} reads {used}, which the site does not define"
        for triple in _TRIPLE.findall(value):
            assert tuple(map(int, triple)) in site_rgb, f"{name}: {value} is not a site colour"
