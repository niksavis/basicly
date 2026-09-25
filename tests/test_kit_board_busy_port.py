from __future__ import annotations

import errno
import importlib.util
import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

SERVER = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "board" / "server.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("basicly_board_kit_server_busy_port", SERVER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


server = _load()


@pytest.fixture
def held_port() -> Iterator[int]:
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    try:
        yield holder.getsockname()[1]
    finally:
        holder.close()


def test_serve_on_a_held_port_refuses_by_name_with_the_free_port_command(
    tmp_path: Path, held_port: int, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()

    code = server.main(["serve", str(ledger), "--port", str(held_port)])

    printed = capsys.readouterr().err
    assert code == server.EXIT_PORT_IN_USE != 0
    assert f"port {held_port} is in use on 127.0.0.1, so nothing was served" in printed
    assert f"serve {ledger} --port {held_port + 1}`" in printed
    assert "`--port 0` to let the system choose a free one" in printed
    assert "Traceback" not in printed


def test_only_the_address_in_use_codes_of_each_platform_read_as_a_busy_port() -> None:
    assert server.address_in_use(OSError(errno.EADDRINUSE, "Address already in use"))
    assert server.address_in_use(OSError(10048, "Only one usage of each socket address"))
    assert not server.address_in_use(OSError(errno.EACCES, "Permission denied"))
    assert not server.address_in_use(OSError(errno.EADDRNOTAVAIL, "Cannot assign address"))


def test_a_bind_error_that_is_not_a_busy_port_still_raises(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()

    with pytest.raises(OSError) as raised:
        server.main(["serve", str(ledger), "--host", "192.0.2.1", "--port", "0"])
    assert not server.address_in_use(raised.value)


def test_the_relaunch_repeats_how_the_server_was_started() -> None:
    by_file = server.relaunch(".basicly/core/kit/board/server.py", ".basicly/ledger")
    assert by_file == "python3 .basicly/core/kit/board/server.py serve .basicly/ledger"
    assert server.relaunch("bin/basicly-board", "ledger") == "basicly-board serve ledger"


def test_a_refused_relaunch_keeps_the_host_it_was_asked_for(
    tmp_path: Path, held_port: int, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()

    code = server.main(["serve", str(ledger), "--host", "localhost", "--port", str(held_port)])

    assert code == server.EXIT_PORT_IN_USE
    assert f"--host localhost --port {held_port + 1}`" in capsys.readouterr().err


def test_the_last_port_suggests_the_default_port_instead_of_one_past_the_range() -> None:
    refusal = server.busy_port_refusal("127.0.0.1", server.MAX_PORT, "basicly-board serve x")
    assert f"--port {server.DEFAULT_PORT}`" in refusal
