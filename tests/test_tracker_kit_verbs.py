from __future__ import annotations

import pytest

from basicly import cli, tracker_kit_verbs


@pytest.mark.parametrize("verb", ["claim", "comment", "create"])
def test_a_kit_only_verb_is_refused_with_the_kit_command(
    verb: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["tracker", verb, "x-1"]) == 2

    shown = capsys.readouterr().err
    assert f"python3 .basicly/core/kit/tracker/cli.py {verb} .basicly/ledger" in shown
    assert "invalid choice" not in shown


def test_no_kit_only_verb_shadows_a_basicly_tracker_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["tracker", "--help"])

    usage = capsys.readouterr().out
    commands = set(usage[usage.index("{") + 1 : usage.index("}")].split(","))
    assert "ready" in commands
    assert not commands & tracker_kit_verbs.KIT_ONLY_VERBS
