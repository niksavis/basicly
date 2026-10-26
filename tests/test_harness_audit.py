from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def test_harness_audit_inventory_has_one_disposition_per_source() -> None:
    root = Path(__file__).resolve().parents[1]
    audit = json.loads((root / "docs/research/harness-audit.json").read_text())
    groups = (
        "skills",
        "agents",
        "fragments",
        "blocks",
        "resources_and_scripts",
        "effective_surfaces",
    )
    paths = [entry["path"] for group in groups for entry in audit[group]]
    counts = Counter(paths)
    assert {path: count for path, count in counts.items() if count > 1} == {}
    assert audit["coverage"]["final_inventory_control"]["source_records"] == len(counts)
    controls = {f".basicly/core/targets/{host}.yaml" for host in ("claude", "codex", "copilot")}
    assert controls <= counts.keys()
    assert "AGENTS.md" in counts
