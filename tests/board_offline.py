"""The one assertion every board page owes: it fetches nothing from an origin.

A board page carries no script and names no origin, so a wall opens it from disk or from the
server with the network off and it renders the same. The one reference a page may carry is the
vendored stylesheet, by the relative path `board_assets.href` spells (basicly-lywzp71); every
other ``<link``, every ``src=`` and every absolute or protocol-relative URL is refused.

Asserted on the raw text rather than by parsing, because the failure this guards is a template
gaining an attribute a parser would have to be taught about first.
"""

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
    """Fail unless *page* references nothing but the vendored stylesheet, relatively."""
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
