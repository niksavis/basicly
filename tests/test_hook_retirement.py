from __future__ import annotations

from typing import TYPE_CHECKING

from basicly.hooks import HookSpec
from basicly.precommit_config import (
    parse_config,
    render_precommit_config,
    retired_hooks_present,
)

if TYPE_CHECKING:
    from pathlib import Path

HOOKS = ".basicly/core/hooks"
LIVE = HookSpec(id="tracker-commit-msg-script", script="tracker-commit-msg.py", stage="commit-msg")

RETIRED_CONFIG = """\
repos:
  - repo: local
    hooks:
      - id: beads-commit-msg-script
        name: beads-commit-msg-script
        entry: uv run python .basicly/core/hooks/beads-commit-msg.py
        language: system
        stages: [commit-msg]
        pass_filenames: false
"""

FOREIGN_CONFIG = """\
repos:
  - repo: local
    hooks:
      # the consumer's own, and not ours to touch
      - id: my-own-check
        name: my-own-check
        entry: ./scripts/my-check.sh
        language: system
        stages: [pre-commit]
"""


def _rendered(existing: str) -> str:
    return render_precommit_config(existing, [LIVE], HOOKS, {LIVE.id})


def test_a_retired_managed_hook_is_pruned() -> None:
    result = _rendered(RETIRED_CONFIG)

    assert "beads-commit-msg" not in result
    assert "tracker-commit-msg.py" in result, "it pruned the live hook too"


def test_a_retired_hook_is_reported_so_the_rewrite_runs(tmp_path: Path) -> None:

    parsed = parse_config(tmp_path / ".pre-commit-config.yaml", RETIRED_CONFIG)

    reasons = retired_hooks_present(parsed, {LIVE.id}, HOOKS)

    assert reasons and "beads-commit-msg-script" in reasons[0]


def test_a_consumers_own_local_hook_survives(tmp_path: Path) -> None:
    parsed = parse_config(tmp_path / ".pre-commit-config.yaml", FOREIGN_CONFIG)

    assert retired_hooks_present(parsed, {LIVE.id}, HOOKS) == []
    result = _rendered(FOREIGN_CONFIG)
    assert "my-own-check" in result
    assert "./scripts/my-check.sh" in result


def test_a_live_managed_hook_is_not_reported_as_retired(tmp_path: Path) -> None:
    current = render_precommit_config(None, [LIVE], HOOKS)
    parsed = parse_config(tmp_path / ".pre-commit-config.yaml", current)

    assert retired_hooks_present(parsed, {LIVE.id}, HOOKS) == []
