from __future__ import annotations

import json
from pathlib import Path

from basicly import claude_settings


def test_an_install_writes_both_cache_ttls_when_absent(tmp_path: Path) -> None:
    assert claude_settings.default_prompt_cache_ttl(tmp_path) is True
    assert claude_settings.default_subagent_cache_ttl(tmp_path) is True

    written = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert written["promptCacheTtl"] == "1h"
    assert written["subagentPromptCacheTtl"] == "1h"
    assert claude_settings.default_prompt_cache_ttl(tmp_path) is False


def test_a_chosen_main_cache_ttl_is_kept(tmp_path: Path) -> None:
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"promptCacheTtl": "5m", "other": 1}), encoding="utf-8")

    assert claude_settings.default_prompt_cache_ttl(tmp_path) is False
    assert json.loads(path.read_text(encoding="utf-8")) == {"promptCacheTtl": "5m", "other": 1}
