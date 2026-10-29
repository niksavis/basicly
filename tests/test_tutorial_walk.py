from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).parent.parent


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


walk = _load(REPO_ROOT / ".scripts" / "tutorial_walk.py", "tutorial_walk")

DOCUMENTED = (
    "Preserved ... generated artifact(s), with inventory: /path/to/myrepo/.git/m.json\n"
    "[merged] merged @ 919b83d8348e into main @ 947a0f9; landing 6.8s "
    "(merge 3.3s, verify 1.8s, probe 1.7s)\n"
)
OBSERVED = (
    "override: runner.default=manual\n"
    "Preserved 46 generated artifact(s), with inventory: /tmp/walk/myrepo/.git/m.json\n"
    "[merged] merged @ de4ac00497dc into main @ 18ecef9; landing 5.9s "
    "(merge 3.0s, probe 1.1s, verify 1.1s)\n"
)


def test_hashes_paths_timings_and_elisions_match_another_run() -> None:
    assert walk.mismatch(DOCUMENTED, OBSERVED) is None


def test_a_changed_count_is_reported_by_its_documented_line() -> None:
    documented = DOCUMENTED.replace("Preserved ...", "Preserved 45")

    assert walk.mismatch(documented, OBSERVED) == documented.splitlines()[0]


def test_blocks_keep_the_fence_kind_and_the_sentence_that_introduces_them() -> None:
    text = "Create `.cfg.json` with:\n\n```json\n{}\n```\n\nRun:\n\n```sh\nls\n```\n"

    found = walk.blocks(text)

    assert [(block.kind, block.body, block.intro) for block in found] == [
        ("json", "{}", "Create `.cfg.json` with:"),
        ("sh", "ls", "Run:"),
    ]
