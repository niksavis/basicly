from __future__ import annotations

import json
import subprocess  # nosec B404
import sys
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

from tests import test_tracker_plugin
from tests.test_kit_board_server import server

package = test_tracker_plugin.package

LEGACY_BOOTSTRAP = """import builtins
import datetime
import http.server
import importlib.util
import sys

if sys.argv[2] == "missing_utc":
    del datetime.UTC
else:
    current_zip = builtins.zip
    def legacy_zip(*iterables):
        return current_zip(*iterables)
    builtins.zip = legacy_zip

spec = importlib.util.spec_from_file_location("basicly_tracker", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
raise SystemExit(module.main(["serve", sys.argv[3], "--port", "0"]))
"""


@pytest.mark.parametrize("interface", ["missing_utc", "legacy_zip"])
def test_packaged_ui_serves_with_simulated_python39_interfaces(
    package: Path, tmp_path: Path, interface: str
) -> None:
    ledger = tmp_path / "ledger"
    created = test_tracker_plugin.run_package(
        package, "create", str(ledger), "--prefix", "demo", "--title", "Legacy runtime card"
    )
    assert created.returncode == 0, created.stderr
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            LEGACY_BOOTSTRAP,
            str(package / "__init__.py"),
            interface,
            str(ledger),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    stderr = process.stderr
    assert stderr is not None
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.append(stderr.readline()), daemon=True)
    reader.start()
    try:
        reader.join(timeout=30)
        assert not reader.is_alive(), "the legacy-interface server did not announce readiness"
        assert lines and "http://" in lines[0], lines
        url = "http://" + lines[0].split("http://", 1)[1].split(" ", 1)[0]
        with urlopen(url + "api/v1/records", timeout=10) as response:
            assert response.status == 200
            records = json.loads(response.read())
        assert "Legacy runtime card" in json.dumps(records)
    finally:
        process.terminate()
        process.wait(timeout=30)
        stderr.close()
        reader.join(timeout=30)


def test_server_refuses_inconsistent_watched_stamp_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "watched.py"
    source.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(server, "loaded_kit_files", lambda: (source,))
    loaded = server.LoadedKit("restart the server")
    monkeypatch.setattr(server, "kit_stamps", lambda _files: ())
    with pytest.raises(ValueError):
        loaded.refuse_a_changed_kit()
