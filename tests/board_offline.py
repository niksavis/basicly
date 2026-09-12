from __future__ import annotations

import re

from basicly import board_assets

LINK = re.compile(r"<link\b[^>]*>")
HREF = re.compile(r'href="([^"]*)"')

ALLOWED_HREFS = frozenset(
    board_assets.href(name, nested=nested)
    for name in board_assets.ASSETS
    for nested in (False, True)
)


def assert_offline(page: str) -> None:
    assert "<script" not in page
    assert "src=" not in page
    assert "http://" not in page
    assert "https://" not in page
    assert 'href="//' not in page
    links = LINK.findall(page)
    assert links, "the page links no stylesheet, so it draws with no component vocabulary"
    for link in links:
        assert 'rel="stylesheet"' in link, link
        found = HREF.search(link)
        assert found is not None, link
        assert found.group(1) in ALLOWED_HREFS, link
