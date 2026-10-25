from __future__ import annotations

REQUEST = ">=3.14"
UV_RUN = "uv run --no-project --no-python-downloads"
COMMAND = f"{UV_RUN} --python '{REQUEST}' python -X utf8"
DISPATCH_COMMAND = f"{UV_RUN} python -X utf8"
ENVIRONMENT = {"UV_PYTHON": REQUEST, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
