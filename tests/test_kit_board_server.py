from __future__ import annotations

import http.client
import importlib.util
import json
import re
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "board"
TRIGGER = "When an export holds a comment, I want it kept, so I can import it back."


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


server = _load(KIT_DIR / "server.py", "basicly_tracker_kit_server")
cli = server.tracker_cli()


class Client:
    def __init__(self, port: int) -> None:
        self.port = port

    def call(
        self,
        method: str,
        path: str,
        body: object = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sent = {"Content-Type": "application/json"} if body is not None else {}
        sent.update(headers or {})
        data = None if body is None else json.dumps(body).encode()
        conn.request(method, path, body=data, headers=sent)
        response = conn.getresponse()
        text = response.read().decode()
        conn.close()
        return response.status, json.loads(text) if text.startswith("{") else {"text": text}


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    directory = tmp_path / "ledger"
    argv = ["create", str(directory), "--prefix", "demo", "--title", "seed"]
    assert cli.main([*argv, "--description", TRIGGER, "--acceptance", "- a"]) == cli.EXIT_OK
    return directory


@pytest.fixture
def client(ledger: Path, capsys: pytest.CaptureFixture[str]) -> Iterator[Client]:
    capsys.readouterr()
    served = server.make_server(ledger, "127.0.0.1", 0)
    thread = threading.Thread(target=served.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(served.server_address[1])
    finally:
        served.shutdown()
        served.server_close()


def test_a_read_returns_the_same_json_as_the_command(
    client: Client, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, served = client.call("GET", "/api/v1/stats")
    cli.main(["stats", str(ledger)])

    assert status == 200
    assert served == json.loads(capsys.readouterr().out)


def test_a_human_draft_waits_for_refinement_until_an_agent_pass_shapes_it(
    client: Client,
) -> None:
    status, made = client.call(
        "POST", "/api/v1/records", {"title": "Keep comments", "fields": {"labels": "refine"}}
    )
    assert status == 201
    record = made["record"]
    _, queue = client.call("GET", "/api/v1/refine")
    assert [row["record"] for row in queue["records"] if row["labelled"]] == [record]

    shaped = {
        "description": TRIGGER,
        "acceptance": "- The export keeps every comment",
        "requirements": "- Standard library only",
        "fields": {"priority": 1},
        "remove_labels": ["refine"],
    }
    status, updated = client.call("PATCH", f"/api/v1/records/{record}", shaped)

    assert status == 200
    assert updated["blocking"] == []
    _, queue = client.call("GET", "/api/v1/refine")
    assert record not in {row["record"] for row in queue["records"]}


def test_an_edit_from_the_page_changes_the_title_and_the_story(client: Client) -> None:
    _, made = client.call("POST", "/api/v1/records", {"title": "draft"})
    edit = {"title": "renamed", "description": TRIGGER, "add_labels": ["refine"]}

    status, _ = client.call("PATCH", f"/api/v1/records/{made['record']}", edit)

    assert status == 200
    _, shown = client.call("GET", f"/api/v1/records/{made['record']}")
    assert shown["fields"]["title"] == "renamed"
    assert shown["fields"]["description"] == TRIGGER


def test_a_kit_refusal_is_a_422_with_the_kit_schema_and_writes_nothing(
    client: Client, ledger: Path
) -> None:
    before = cli.events.read_events(ledger)[0]

    status, report = client.call("POST", "/api/v1/records", {"fields": {"design": "x"}})

    assert status == 422
    assert report["schema"] == "basicly.tracker.create.v2"
    assert "import history" in report["refused"]
    assert cli.events.read_events(ledger)[0] == before


def test_a_value_that_looks_like_a_flag_is_stored_as_text(client: Client) -> None:
    _, made = client.call("POST", "/api/v1/records", {"title": "--status=closed"})
    _, shown = client.call("GET", f"/api/v1/records/{made['record']}")

    assert shown["fields"]["title"] == "--status=closed"
    assert shown["status"] == "open"


@pytest.mark.parametrize(
    "case",
    [
        ("POST", "/api/v1/records", {"title": "x"}, {"Origin": "http://evil.example"}, 403),
        ("GET", "/api/v1/stats", None, {"Host": "evil.example:80"}, 403),
        ("POST", "/api/v1/records", {"title": "x"}, {"Content-Type": "text/plain"}, 400),
        ("POST", "/api/v1/records", {"title": "x", "colour": "red"}, {}, 400),
        ("GET", "/api/v1/records/NOT_AN_ID", None, {}, 400),
        ("GET", "/api/v1/records/demo-zzzz", None, {}, 404),
        ("GET", "/api/v1/nothing", None, {}, 404),
        ("GET", "/%2e%2e/server.py", None, {}, 404),
    ],
)
def test_a_request_the_server_must_not_serve_is_refused_by_name(
    client: Client, ledger: Path, case: tuple[str, str, object, dict[str, str], int]
) -> None:
    method, path, body, headers, expected = case
    before = cli.events.read_events(ledger)[0]

    status, report = client.call(method, path, body, headers)

    assert status == expected
    assert report.get("refused") or report.get("found") is False
    assert cli.events.read_events(ledger)[0] == before


def test_the_index_lists_every_endpoint_and_the_page_is_served(client: Client) -> None:
    _, index = client.call("GET", "/api/v1")
    status, page = client.call("GET", "/")

    assert index["schema"] == "basicly.tracker.api.v1"
    assert any("/refine" in one for one in index["endpoints"])
    assert status == 200
    assert "/api/v1" in page["text"]


def test_a_custom_page_directory_replaces_the_default_page(ledger: Path, tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<p>our own board</p>", encoding="utf-8")
    served = server.make_server(ledger, "127.0.0.1", 0, web=web)
    thread = threading.Thread(target=served.serve_forever, daemon=True)
    thread.start()
    try:
        _, page = Client(served.server_address[1]).call("GET", "/")
    finally:
        served.shutdown()
        served.server_close()

    assert page["text"] == "<p>our own board</p>"


def test_a_second_person_reserving_through_the_api_is_refused_by_name(client: Client) -> None:
    _, made = client.call("POST", "/api/v1/records", {"title": "reserve me"})
    record = made["record"]

    status, _ = client.call("POST", f"/api/v1/records/{record}/assign", {"to": "alex"})
    assert status == 200
    status, refused = client.call("POST", f"/api/v1/records/{record}/claim", {"to": "sam"})
    assert status == 422
    assert "held by alex" in refused["refused"]

    status, _ = client.call("POST", f"/api/v1/records/{record}/claim", {"to": "sam", "take": True})
    assert status == 200
    _, shown = client.call("GET", f"/api/v1/records/{record}")
    assert (shown["status"], shown["holder"]["name"]) == ("in_progress", "sam")
    _, ready = client.call("GET", "/api/v1/ready")
    assert all("holder" in row for row in ready["records"])


def test_a_record_read_carries_each_comment_with_its_writer_and_time(client: Client) -> None:
    _, made = client.call("POST", "/api/v1/records", {"title": "talk about me"})
    record = made["record"]
    client.call("POST", f"/api/v1/records/{record}/comments", {"text": "first"})
    client.call("POST", f"/api/v1/records/{record}/comments", {"text": "second"})

    _, shown = client.call("GET", f"/api/v1/records/{record}")

    assert [one["text"] for one in shown["comment_log"]] == shown["comments"] == ["first", "second"]
    assert all(one["writer"] and one["at"] for one in shown["comment_log"])


def test_a_record_read_names_the_title_of_each_record_it_depends_on(client: Client) -> None:
    _, first = client.call("POST", "/api/v1/records", {"title": "the blocker"})
    _, second = client.call("POST", "/api/v1/records", {"title": "the dependent"})
    client.call("POST", f"/api/v1/records/{second['record']}/deps", {"target": first["record"]})

    _, shown = client.call("GET", f"/api/v1/records/{second['record']}")

    assert [edge["title"] for edge in shown["dependencies"]] == ["the blocker"]


def test_the_index_names_the_holder_a_page_calls_you(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "alex")

    _, index = client.call("GET", "/api/v1")

    assert index["holder"] == "alex"
    assert index["edge_types"] == ["blocks", "discovered-from", "parent-child", "related"]


PAGE = (KIT_DIR / "web" / "index.html").read_text(encoding="utf-8")


def test_every_action_the_page_posts_is_a_route_the_server_takes() -> None:
    posted = set(re.findall(r'\bact\("([a-z]+)"', PAGE))

    assert posted
    assert posted <= set(server._ACTIONS)


def test_every_key_the_page_patches_is_one_the_server_accepts() -> None:
    patched = set(re.findall(r"patch\(\{ ?\"?([a-z_]+)\"?:", PAGE))
    content = re.search(r"const CONTENT = \[([^\]]*)\]", PAGE)
    assert content
    edited = set(re.findall(r'"([a-z_]+)"', content.group(1)))
    assigned = set(re.findall(r"\bbody\.([a-z_]+) =", PAGE))

    assert patched and edited and assigned
    assert patched | edited | assigned <= server._UPDATE_KEYS


def test_every_list_the_page_reads_is_a_read_the_server_answers() -> None:
    reads = set(re.findall(r'call\("GET", "/([a-z]+)', PAGE))

    assert reads
    assert reads <= {*server.READS, "records"}
