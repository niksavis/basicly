from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".basicly/core/hooks/orch-commit-note.py"
sys.path.insert(0, str(ROOT / ".basicly" / "core" / "kit" / "orch"))


def test_a_commit_writes_its_record_and_subject_to_the_session_state(tmp_path: Path) -> None:
    clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=clean)
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subject = "fix(demo): keep the work (acme-7)"
    subprocess.run(
        ["git", "-C", str(tmp_path), *identity, "commit", "-q", "--allow-empty", "-m", subject],
        check=True,
        env=clean,
    )
    lessons = tmp_path / ".orchestrator" / "lessons"
    lessons.mkdir(parents=True)
    (lessons / importlib.import_module("orch_lessons.log").MARKER).write_text("", "utf-8")

    done = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        env={**clean, "ORCH_SESSION_ID": "s3"},
        check=False,
    )

    assert done.returncode == 0
    saved = json.loads((tmp_path / ".orchestrator/sessions/s3.json").read_text(encoding="utf-8"))
    assert (saved["item"], saved["last"]) == ("acme-7", f"committed: {subject}")
