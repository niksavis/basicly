from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def seed_native_target(cli: Any, tmp_path: Path, bare: Path, record: str) -> None:
    events = cli.events
    made = events.append(
        tmp_path / "native-source",
        [
            events.Draft(record, events.KIND_CREATED, {"title": "A real native comment target"}),
            events.Draft(record, events.KIND_STATUS, {"status": "open"}),
        ],
    )
    state = tmp_path / "machines" / "fixture-publisher"
    clone = cli.vault_cli.vault_clone.locate(state, str(bare))
    vault_queue = cli.vault_cli.vault_queue
    drafts = [
        {"kind": "ledger", "record": event.record, "ledger": json.loads(events.to_json(event))}
        for event in made
    ]
    vault_queue.write(
        clone, drafts, state=state, origin=vault_queue.Origin("fixture", False, time.time)
    )
    cli.vault_cli.vault_share.share(clone, state, plan=cli.vault_cli.vault_share.Plan(time.time))
