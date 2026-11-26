from __future__ import annotations

import json
import time
from pathlib import Path

DIR_ENV = "BASICLY_CAPABILITIES_DIR"


def capability_file(folder: Path, written_at: float | None = None, **changed: object) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    value = {
        "format": "basicly.capabilities",
        "version": 1,
        "writer": "handily-reply-view",
        "writer_version": "0.34.1",
        "written_at": time.time() if written_at is None else written_at,
        "renders": {"mermaid": ["flowchart", "sequenceDiagram"]},
        **changed,
    }
    path = folder / "handily-reply-view.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path
