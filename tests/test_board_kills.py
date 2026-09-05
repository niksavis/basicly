"""The verb that stops a running lane, and the card it is offered on.

`lane-kill` has been in `board_actions.ACTIONS` since the action surface landed, and
`board_asks.pending` builds a row per **ask** - so it was pressable exactly when something
else happened to be waiting. A verb that exists and cannot be reached is worse than one that
does not, because a reader of the code believes it is available (basicly-x1h1dl5).

The join is the part worth asserting. The cards are a bounded slice of `lanes[]` and the
forms are every lane, so a positional pairing arms one card with another lane's id - a
button that stops the wrong work, and one no green suite would notice.
"""

from __future__ import annotations

import re

from basicly import board_asks
from tests.test_board_asks import TOKEN, _page


def test_a_running_lane_is_offered_the_verb_that_stops_it() -> None:
    """The verb worked and had no surface: it was pressable only when an ask prefilled it."""
    lanes = [{"id": "basicly-rbnz49"}, {"id": "basicly-7bur"}]
    forms = board_asks.killable(lanes, TOKEN)
    assert sorted(forms) == ["basicly-7bur", "basicly-rbnz49"], "one form per running lane"
    row = forms["basicly-rbnz49"]
    assert row["action"] == board_asks.KILL
    filled = {field["name"]: field["value"] for field in row["fields"]}
    assert filled["issue"] == "basicly-rbnz49", "the lane it stops is filled in, not retyped"
    assert filled["reason"] == "", "why is a person's to give, and the ledger keeps it"
    assert "basicly loop kill basicly-rbnz49" in row["command"]


def test_no_lane_is_offered_a_form_naming_an_empty_id() -> None:
    """The record's own words. A button armed with "" kills whatever the CLI resolves that to."""
    assert board_asks.killable(None, TOKEN) == {}
    assert board_asks.killable([], TOKEN) == {}
    assert board_asks.killable([{"branch": "harness/x"}, {"id": ""}], TOKEN) == {}


def test_the_kill_form_lands_on_the_card_that_draws_its_own_lane() -> None:
    """A card is armed with its own lane and no other.

    Keyed by id because the cards are a bounded slice of the lanes, and a positional join
    arms one card with another lane's id - a button that stops the wrong work.
    """
    page = _page([])
    cards = page.split('<div class="card')[1:]
    assert cards, "the fixture draws no lane cards, so this proves nothing"
    for card in cards:
        ident = re.search(r'<dd class="clip">\s*(basicly-[\w.]+)', card)
        form = re.search(r'name="issue" value="(basicly-[\w.]+)"', card)
        assert ident is not None and form is not None, "a lane card carries no kill form"
        assert form.group(1) == ident.group(1), "a card is armed with another lane's id"
