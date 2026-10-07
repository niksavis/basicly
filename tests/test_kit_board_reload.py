from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

from tests.kit_board_client import Client
from tests.test_kit_board_server import TRIGGER, cli, server

reload = server.reload


class _Server:
    def __init__(self) -> None:
        self.shutdowns = 0

    def shutdown(self) -> None:
        self.shutdowns += 1


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _kit(watched: Path) -> SimpleNamespace:
    watched.write_text("VALUE = 1\n", encoding="utf-8")
    return SimpleNamespace(files=(watched,), stamps=server.kit_stamps((watched,)))


def _watcher(target: object, kit: SimpleNamespace, **kw: object) -> tuple:
    clock, logs = _Clock(), []
    watcher = reload.Watcher(target, kit, log=logs.append, **kw)
    watcher.clock = clock
    return watcher, clock, logs


def test_a_changed_kit_restarts_after_its_probe_passes(tmp_path: Path) -> None:
    fake = _Server()
    watcher, clock, logs = _watcher(fake, _kit(tmp_path / "routes.py"))

    assert watcher.check() is False
    (tmp_path / "routes.py").write_text("VALUE = 22\n", encoding="utf-8")
    assert watcher.check() is False
    clock.now += reload.Watcher.settle_s

    assert watcher.check() is True
    assert watcher.due is True
    assert fake.shutdowns == 1
    assert "restarts on the new kit" in logs[-1]


def test_a_kit_still_changing_inside_the_settle_window_does_not_restart(tmp_path: Path) -> None:
    fake = _Server()
    watched = tmp_path / "routes.py"
    watcher, clock, _ = _watcher(fake, _kit(watched))

    watched.write_text("VALUE = 22\n", encoding="utf-8")
    assert watcher.check() is False
    clock.now += reload.Watcher.settle_s - 1
    watched.write_text("VALUE = 333\n", encoding="utf-8")
    assert watcher.check() is False
    clock.now += reload.Watcher.settle_s - 1

    assert watcher.check() is False
    assert fake.shutdowns == 0


def test_a_failed_probe_keeps_refusing_with_the_named_file(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    argv = ["create", str(ledger), "--prefix", "demo", "--title", "seed"]
    assert cli.main([*argv, "--description", TRIGGER, "--acceptance", "- a"]) == cli.EXIT_OK
    served = server.make_server(ledger, "127.0.0.1", 0)
    kit = served.RequestHandlerClass.kit
    watched = tmp_path / "routes.py"
    stub = _kit(watched)
    kit.files, kit.stamps = stub.files, stub.stamps
    watcher, clock, logs = _watcher(served, kit)
    thread = threading.Thread(target=served.serve_forever, daemon=True)
    thread.start()
    try:
        watched.write_text("VALUE = (((\n", encoding="utf-8")
        assert watcher.check() is False
        clock.now += reload.Watcher.settle_s
        assert watcher.check() is False
        status, report = Client(served.server_address[1]).call("GET", "/api/v1/version")
    finally:
        served.shutdown()
        served.server_close()

    assert watcher.due is False
    assert status == 503
    assert "routes.py" in report["refused"]
    assert "does not load" in logs[-1]
    assert "SyntaxError" in logs[-1]


def test_the_probe_passes_this_kit_and_names_a_broken_file(tmp_path: Path) -> None:
    broken = tmp_path / "routes.py"
    broken.write_text("VALUE = (\n", encoding="utf-8")

    kit = Path(server.__file__).resolve().parent.parent
    assert reload.probe(sorted((*kit.glob("board/*.py"), *kit.glob("tracker/*.py")))) is None
    assert "routes.py: SyntaxError" in (reload.probe((broken,)) or "")
