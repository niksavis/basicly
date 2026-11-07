from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / ".basicly/core/skills/swarm/scripts/swarm.py"
_spec = importlib.util.spec_from_file_location("swarm_script", SCRIPT)
assert _spec is not None and _spec.loader is not None
swarm = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = swarm
_spec.loader.exec_module(swarm)


def _reply(directory: Path, lens: str, payload: dict | None, prose: str = "Done.\n") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    full = None if payload is None else {"lens": lens, "verdict": "pass", **payload}
    block = "" if full is None else f"```json\n{json.dumps(full)}\n```\n"
    (directory / f"{lens}.md").write_text(prose + block, encoding="utf-8")


def _finding(severity: str, evidence: str, claim: str = "c") -> dict:
    return {"severity": severity, "claim": claim, "evidence": evidence, "fix": "f"}
