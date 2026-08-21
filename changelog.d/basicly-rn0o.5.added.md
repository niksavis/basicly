- **`basicly board serve` puts the board on a wall display, read only.** It binds
  `127.0.0.1` and nothing else, answers `GET /` with the page and
  `GET /snapshot.json` with the `harness-board/v1` contract, and returns 405 to any
  POST — the action surface is a separate unit and a screen anyone in the room can
  touch cannot kill a lane. While a supervisor lock is fresh it serves that
  producer's snapshot bytes and folds nothing; otherwise it folds for itself every
  `--refresh` seconds (default 15, the supervisor's own heartbeat) and keeps the
  result in memory. The process takes no lock and writes no file, so a board can
  never be the reason a gate or a landing failed, and Ctrl-C reports how many
  refreshes it managed (`basicly-rn0o.5`).
