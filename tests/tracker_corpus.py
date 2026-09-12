from __future__ import annotations

import json
from pathlib import Path

from basicly import config, tracker

REPO_ROOT = Path(__file__).resolve().parent.parent


def snapshot_text(repo_root: Path = REPO_ROOT) -> str:

    config.load_tracker_mode(repo_root)
    lines = []
    for record in tracker.all_records(repo_root):
        row = {key: value for key, value in record.items() if key != "dependencies"}
        row["dependencies"] = [
            {"depends_on_id": edge["id"], "type": edge["dependency_type"]}
            for edge in record.get("dependencies", ())
        ]
        lines.append(json.dumps(row))
    return "".join(line + "\n" for line in lines)
