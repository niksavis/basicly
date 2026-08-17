"""This repository's own records, rendered in the shape the kit's importer reads.

The corpus the import and differential tests run against. It was the committed export
until that store was deleted (basicly-vkh0.42.7); it is derived rather than fixed for the
same reason it was the export before — the subject has to be *this tracker's* real
history, at real scale, or the claim is about a fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

from basicly import config, tracker

REPO_ROOT = Path(__file__).resolve().parent.parent


def snapshot_text(repo_root: Path = REPO_ROOT) -> str:
    """*repo_root*'s records as the importer's JSONL.

    The importer reads a *foreign* store, so the edge spelling is the foreign one. The
    `config` call is for its side effect: it installs the mode reader.
    """
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
