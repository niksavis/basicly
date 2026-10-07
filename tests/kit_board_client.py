from __future__ import annotations

import http.client
import json
from typing import Any


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
