"""The marks: that each is the vendored path, that the roster is closed, and that it draws.

The claim is not "an icon rendered". Four properties carry the whole design and each is
asserted against a refutation:

* **The page's path is the vendored file's path, byte for byte.** Asserted by parsing the
  vendored SVG with :mod:`xml.etree.ElementTree` — a second parser sharing no step with
  :mod:`basicly.board_icons`' own regex — and then looking for that exact string in the
  rendered page. A copy that drifted, a transform that mangled it, or a template that lost
  the attribute all fail here.
* **The roster is closed.** Asserted by listing the vendored directory, not by reading
  :data:`~basicly.board_icons.ICONS`, so committing the release's other 2,071 icons fails.
* **Each mark discriminates.** Every band state is rendered and asserted to carry its own
  path *and none of the other four* — the over-trigger direction. A mark drawn for every
  state would satisfy "the band has a mark" and tell a reader nothing.
* **The page still references nothing.** No ``<script``, no ``<link``, no ``src=``, and no
  codepoint arrow in either spelling. ``tests/test_board_render.py`` owns the first three
  for the page as a whole; they are re-asserted here because an icon is exactly the change
  that would have reached for a font, a stylesheet or a sprite.

The states the band can hold are built here rather than looked for in a fixture: three of
the five (``calm``, ``stuck``, ``withheld``) appear in no checked-in board, so a suite that
used only fixtures would leave three of the seven vendored icons undrawn and unnoticed.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from basicly import board_icons, board_regions, board_render, board_schema
from tests.test_board_wall import FIXTURES, REPO_ROOT, STAMPED

TEMPLATES = REPO_ROOT / ".basicly" / "core" / "templates" / "board"
ICONS_DIR = TEMPLATES / board_icons.DIRNAME
_SVG = "{http://www.w3.org/2000/svg}"

# The band's five, and how to force each one out of `board_regions.band`'s precedence.
# `withheld` is a present-but-non-conformant `asks` section; `absent` is no section at all.
_BAND_STATES = ("calm", "waiting", "stuck", "withheld", "absent")


def _vendored(icon: str) -> tuple[str, bool]:
    """*icon*'s single path and its winding rule, parsed independently of `board_icons`."""
    root = ElementTree.parse(ICONS_DIR / f"{icon}.svg").getroot()
    paths = root.findall(f"{_SVG}path")
    assert len(paths) == 1, f"{icon} holds {len(paths)} paths"
    return paths[0].attrib["d"], paths[0].get("fill-rule") == "evenodd"


def _document(state: str) -> dict[str, Any]:
    """A board whose watch band reads *state*, built off the densest checked-in fixture."""
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
    """Every CSS `content` value in *page* that is not plain ASCII text.

    Comments are stripped first. A `content` declaration is code; a comment naming one is
    prose, and a probe that cannot tell them apart reports the documentation.
    """
    code = re.sub(r"/\*.*?\*/", "", page, flags=re.DOTALL)
    return [
        value
        for value in re.findall(r'content:\s*"([^"]*)"', code)
        if not value.isascii() or "\\" in value
    ]


def _band(page: str) -> str:
    """The watch band's own section of *page*, so a mark elsewhere cannot be mistaken for it."""
    found = re.search(r'<section class="region band framed.*?</section>', page, re.DOTALL)
    assert found is not None, "the page drew no band"
    return found.group(0)


def _page(doc: dict[str, Any], *, after_s: float = 8.0) -> str:
    """*doc* drawn as the page, against an injected instant."""
    now = STAMPED + timedelta(seconds=after_s)
    return board_render.page(
        doc, board_schema.verdict(REPO_ROOT, doc), now=now, templates_dir=TEMPLATES
    )


@pytest.mark.parametrize("mark", sorted(board_icons.ICONS))
def test_a_mark_carries_the_vendored_path_and_its_winding_rule(mark: str) -> None:
    """Read against a second parser, so the two agree on the path rather than on one reading.

    The winding rule is asserted too: dropping it does not fail, it fills the hole in
    `arrow-right-short` and renders a blob that still reads as a mark. And the recorded
    digest is derived from the independent parse, so the pin is checked against the file
    rather than against itself.
    """
    icon = board_icons.ICONS[mark]
    path, even_odd = _vendored(icon)
    read = board_icons.read_mark(ICONS_DIR, mark)
    assert (read.path, read.even_odd) == (path, even_odd)
    assert board_icons.DIGESTS[icon] == board_icons.digest(
        board_icons.Mark(path=path, even_odd=even_odd)
    )


def test_the_even_odd_rule_discriminates_between_two_vendored_icons() -> None:
    """One icon winds even-odd and another does not, so the flag is read and not defaulted."""
    assert board_icons.read_mark(ICONS_DIR, "chain").even_odd is True
    assert board_icons.read_mark(ICONS_DIR, "checkpoint").even_odd is False


def test_the_vendored_directory_holds_only_the_icons_the_roster_names() -> None:
    """Listed off disk, so embedding the release's other 2,071 icons fails here.

    The licence and the provenance note are required beside them: a vendored file with no
    stated version and no licence is a copy nobody can audit or refresh.
    """
    assert {path.stem for path in ICONS_DIR.glob("*.svg")} == set(board_icons.ICONS.values())
    assert (ICONS_DIR / "LICENSE").is_file()
    assert board_icons.VERSION in (ICONS_DIR / "PROVENANCE.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("state", _BAND_STATES)
def test_the_band_draws_its_own_state_mark_and_no_other(state: str) -> None:
    """The discriminating direction: a mark drawn for every state would say nothing.

    So each of the five is rendered and the other four are asserted absent. Scoped to the
    band's own section, because `waiting`'s hourglass has a second site — a station a person
    is blocking — and a whole-page assertion would read that as the band drawing two marks.
    """
    band = _band(_page(_document(state)))
    marks = board_icons.marks(TEMPLATES)
    assert marks[state].path in band
    others = [other for other in _BAND_STATES if other != state and marks[other].path in band]
    assert not others, f"the {state} band also drew {others}"


def test_the_loop_and_the_queue_draw_their_marks_from_the_vendored_files() -> None:
    """The checkpoint and the chain hop, each asserted as the vendored string in the page."""
    page = _page(_document("waiting"))
    for mark in ("checkpoint", "chain"):
        path, _ = _vendored(board_icons.ICONS[mark])
        assert page.count(f'd="{path}"') >= 1, f"the page drew no {mark}"


def test_the_chain_hop_carries_its_winding_rule_into_the_page() -> None:
    """Rendered rather than asserted on the model, because the template writes the attribute."""
    page = _page(_document("waiting"))
    path, _ = _vendored(board_icons.ICONS["chain"])
    assert f'd="{path}" fill-rule="evenodd"' in page


@pytest.mark.parametrize("state", _BAND_STATES)
def test_the_page_references_no_origin_and_no_css_declaration_emits_a_codepoint(
    state: str,
) -> None:
    r"""A mark is an inline path, so the page a wall opens with no network is unchanged.

    The second half is the general form of the arrow this record removed: `content: "\2192"`
    put a codepoint on the wall spelled as a CSS escape, so it was invisible both to a search
    of the rendered bytes and to the page's own no-codepoint rule. Every `content` value is
    checked rather than that one arrow, because naming the arrow would pass the next escape.
    """
    page = _page(_document(state))
    assert "<script" not in page
    assert "<link" not in page
    assert "src=" not in page
    # The positive control, and it is not optional: the CSS comment recording *why* the
    # arrow went spells the declaration verbatim, so a probe that read prose reported the
    # comment and the same probe would have reported a clean page as dirty.
    assert _content_codepoints(page + '\na::before { content: "\\2192"; }\n') == ["\\2192"]
    assert _content_codepoints(page) == []


def test_a_vendored_file_holding_two_paths_is_refused(tmp_path: Path) -> None:
    """The case that must fail loudly: a second path dropped in silence still draws a mark."""
    icon = board_icons.ICONS["checkpoint"]
    one, _ = _vendored(icon)
    (tmp_path / f"{icon}.svg").write_text(
        f'<svg viewBox="0 0 16 16"><path d="{one}"/><path d="M0 0h1v1z"/></svg>',
        encoding="utf-8",
    )
    with pytest.raises(board_icons.IconError, match="holds 2 paths"):
        board_icons.read_mark(tmp_path, "checkpoint")


def test_a_vendored_file_on_another_grid_is_refused(tmp_path: Path) -> None:
    """A 24-unit icon on a 16-unit grid renders at the wrong size rather than not at all."""
    icon = board_icons.ICONS["checkpoint"]
    one, _ = _vendored(icon)
    (tmp_path / f"{icon}.svg").write_text(
        f'<svg viewBox="0 0 24 24"><path d="{one}"/></svg>', encoding="utf-8"
    )
    with pytest.raises(board_icons.IconError, match="16x16 grid"):
        board_icons.read_mark(tmp_path, "checkpoint")


def test_a_missing_vendored_file_names_the_path_it_looked_for(tmp_path: Path) -> None:
    """The operand in the message, so the error names the file a refresh forgot to copy."""
    with pytest.raises(board_icons.IconError, match=re.escape(str(tmp_path))):
        board_icons.marks(tmp_path)
