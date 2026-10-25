from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_rebuild_refuses_unverified_source_before_building(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "source.tar.gz").write_bytes(b"unverified source")
    output = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / ".scripts/build_lefthook_windows.py"),
            "--source-dir",
            str(source),
            "--out",
            str(output),
            "--check",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode != 0
    assert "Lefthook checksum mismatch: source.tar.gz" in result.stderr
    assert not output.exists()
