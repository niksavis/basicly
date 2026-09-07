"""The gate: a record the board names as a thing to act on is named by its title.

Owner, four sessions running, most recently 2026-09-06: *"the real titles of features and tasks
are more important than IDs. IDs should be there but focus is on titles"*. Each site has been
fixed as its own defect - `basicly-a8jy77` for the header, `basicly-lc2bd3v.2` for the record
page - and it keeps coming back, which is what says the review was of the wrong thing.

So this is a check the page satisfies on every render rather than a judgement made once. It
fails naming the region, so a sixth site cannot be added silently (basicly-lc2bd3v.8).
"""

from __future__ import annotations

import html
import re
from typing import Any

import pytest

from basicly import board_graph, board_regions, board_schema, board_wall
from tests.test_board_operator_questions import REPO_ROOT
from tests.test_board_operator_questions import page as _wall
from tests.test_board_wall import document

# An element whose class is here draws a *shape* and not members, so its ids stand alone. The
# dependency chain is the case the acceptance names: `a -> b -> c -> d` cannot carry four
# titles and stay one line. Enumerable rather than implicit - adding a class here is a visible
# claim that the region draws a shape.
SHAPE_ELEMENTS = board_graph.SHAPE_ELEMENTS

REGION = re.compile(r'<section class="region ([\w-]+)[^"]*"[^>]*>(.*?)</section>', re.DOTALL)


def _without_shapes(chunk: str) -> str:
    """*chunk* with every declared shape element removed, so its ids are not scanned."""
    for name in SHAPE_ELEMENTS:
        chunk = re.sub(rf'<div class="{name}">.*?</div>', " ", chunk, flags=re.DOTALL)
    return chunk


def _named(chunk: str, known: frozenset[str]) -> set[str]:
    """The record ids *chunk* prints as text, never the ones that appear only in an href.

    Tags are stripped before the scan and not pattern-matched around: an `href` carries the id
    of the page behind a link, and counting that as a printed name is how a probe reports a
    bare id on a row whose title is right there. Matched against the document's own ids rather
    than by a shape, because `basicly-x.html` and `basicly-x.7` both match one.
    """
    text = html.unescape(re.sub(r"<[^>]+>", " ", chunk))
    tokens = set(re.findall(r"[\w.-]+", text))
    return {ident for ident in known if ident in tokens}


def _titled(chunk: str, title: str) -> bool:
    """Whether *chunk* carries *title*, whole or clipped, as text or as a `title` attribute."""
    text = html.unescape(chunk)
    # `board_wall.clip` truncates and marks; the first half is enough to prove the row drew a
    # title rather than that it drew this exact string.
    return title in text or (len(title) > 24 and title[:24] in text)


def _bare(page: str, titles: dict[str, str]) -> dict[str, set[str]]:
    """Every region drawing a record id with no title, keyed by region."""
    body = page[page.index("<body") :]
    known = frozenset(titles)
    found: dict[str, set[str]] = {}
    for name, chunk in REGION.findall(body):
        scanned = _without_shapes(chunk)
        missing = {ident for ident in _named(scanned, known) if not _titled(scanned, titles[ident])}
        if missing:
            found[name] = missing
    return found


@pytest.fixture
def doc() -> dict[str, Any]:
    """The wall fixture, parsed fresh so a mutating test cannot reach another one."""
    return document("wall-v1.json")


def test_no_region_names_a_record_by_id_alone(doc: dict[str, Any]) -> None:
    """The gate. It fails naming the region, because "the board does it somewhere" is not fixable.

    Measured on the live fold of 2026-09-07 before the fix: 13 bare ids over three regions -
    the parked strip, the queue's unblocks-most list and the events ticker.
    """
    # The richer page, not `test_board_asks._page`: no shipped fixture carries a deferred
    # record, so the parked strip draws nothing there and the gate would pass a page it never
    # scanned. A fixture thinner than production is how a region ships unchecked.
    doc["units"] = [
        *doc["units"],
        {"id": "basicly-parked1", "status": "deferred", "title": "parked work", "priority": "P3"},
    ]
    page = _wall()
    titles = board_regions.unit_titles(_reads(doc))
    assert titles, "the fixture carries no titled unit, so this proves nothing"
    bare = _bare(page, titles)
    assert not bare, "; ".join(
        f"region {name!r} names {sorted(ids)} with no title" for name, ids in sorted(bare.items())
    )


def test_the_gate_catches_a_region_that_drops_a_title(doc: dict[str, Any]) -> None:
    """The positive control. A gate nobody has watched fail is a gate nobody has tested.

    Without this the whole check passes on a page it never actually scanned - an empty probe
    and a clean page are one result, and the second is the rare one.
    """
    titles = board_regions.unit_titles(_reads(doc))
    ident, title = next(iter(titles.items()))
    faked = f'<body><section class="region ready"><span>{ident}</span></section></body>'
    caught = _bare(faked, {ident: title})
    assert caught == {"ready": {ident}}, "the gate does not see a bare id it is pointed at"


def test_a_declared_shape_element_is_the_only_exemption() -> None:
    """The chain draws `a -> b -> c -> d`, which cannot carry four titles and stay one line.

    Pinned as a list rather than a habit: an exemption that is implicit is one nobody reviews,
    and this is the record's own condition for allowing any.
    """
    assert SHAPE_ELEMENTS == ("chain",), (
        "a second element claims to draw a shape; that claim belongs in a review, not a diff"
    )


def test_an_id_the_board_draws_is_never_clipped_out_of_its_own_title_lookup() -> None:
    """The template keys `titles` by the id a region printed, so a clipped id finds nothing.

    Latent rather than live: the longest id on this repository is 18 characters against a
    24-character bound, so nothing is clipped today and the lookup would fail in silence on
    the first record whose id is longer.
    """
    doc = document("wall-v1.json")
    longest = max((str(row["id"]) for row in doc["units"] if row.get("id")), key=len)
    assert len(longest) <= board_graph.ID_MAX, (
        f"{longest!r} is clipped by ID_MAX={board_graph.ID_MAX}, so the queue would print an "
        "id the title map cannot be keyed on"
    )


def _reads(doc: dict[str, Any]) -> Any:
    """The document's readings, which `unit_titles` joins over."""
    return board_wall.readings(doc, board_schema.verdict(REPO_ROOT, doc))
