from __future__ import annotations

import re
from pathlib import Path

from basicly import hooks

TUTORIAL = Path(__file__).parent.parent / "docs" / "tutorial" / "first-loop.md"
HOOK_LINE = re.compile(
    r"^\s*hooks: (?P<stages>[a-z, -]+) — repository-local Lefthook hooks installed$"
)


def test_every_tutorial_hook_line_lists_the_shipped_stages_in_order() -> None:
    shown = [
        found["stages"]
        for line in TUTORIAL.read_text(encoding="utf-8").splitlines()
        if (found := HOOK_LINE.match(line))
    ]

    assert shown, "the tutorial shows no worktree hook line"
    expected = ", ".join(hooks.hook_stages(hooks.load_hook_specs()))
    assert set(shown) == {expected}
