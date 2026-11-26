# Usage snapshot

Status: version 1, 2026-11-26. Tracker: `basicly-qpr2xmr.3.39`, `basicly-qpr2xmr.3.40`.

## 1. Problem

The Orchestrator plans the work of each 5-hour Claude usage window. Usage must end near 100% at the reset and must not reach the limit early.
Claude Code gives the used percent of the 5-hour and 7-day windows only on the standard input of the status line command.
No hook, OpenTelemetry metric or session channel gives these values (Claude Code 2.1.332 documentation, read 2026-11-26).

## 2. Source of the values

- The status line input carries `rate_limits.five_hour` and `rate_limits.seven_day`. Each has `used_percentage` (0 to 100) and `resets_at` (Unix epoch seconds) (code.claude.com/docs/en/statusline, read 2026-11-26).
- `rate_limits` is present only for claude.ai Pro, Max and Ultra subscribers, and only after the first API response of the session. Each window can be absent. Claude Code drops a window when its `resets_at` time passes.
- The status line runs at session start, at each new assistant message, after `/compact`, and on the other events that the documentation names. Claude Code debounces updates at 300 ms and cancels a run that is still in progress when a new update starts.
- Without `refreshInterval`, an idle session does not run the status line again. The percent is for the whole account, so other clients (claude.ai, other machines) can raise it while every local session is idle. The snapshot then goes stale and `orch pace` refuses.

## 3. The files

Each writer writes its own file in one folder. The reader reads every file in the folder. A half-written file costs one skipped reading, never a wrong number (Orchestrator decision of 2026-11-26 on `handily-l97t2x`).

| Item | Value |
| --- | --- |
| Folder | `BASICLY_USAGE_SNAPSHOT_DIR` when set; else `$XDG_STATE_HOME/basicly/usage-snapshots/` (an absolute `XDG_STATE_HOME` only, else `~/.local/state`); on Windows `%LOCALAPPDATA%\\basicly\\usage-snapshots\\` |
| File | `<writer>.json`. The writer name is lower-case letters, digits and dashes, at most 40 characters |
| Writers | `statusline` (`orch-statusline-tap`, basicly-orch package), `handily-board` |
| Owner | basicly owns the format. Other writers write the same format and read nothing of basicly |
| Format | JSON, UTF-8 |

A writer computes the folder itself. It needs no basicly code.

### Fields

| Field | Unit and source |
| --- | --- |
| `format` | always `basicly.usage-snapshot` |
| `version` | integer, `1` |
| `writer` | the writer name; it equals the file name without `.json` |
| `written_at` | Unix epoch seconds of the write |
| `windows.five_hour`, `windows.seven_day` | one window object each; a window can be absent |
| window `used_percentage` | number, 0 to 100, from `rate_limits.<window>.used_percentage` |
| window `resets_at` | integer, Unix epoch seconds, from `rate_limits.<window>.resets_at`. A writer that gets ISO text converts it |
| window `read_at` | integer, Unix epoch seconds of the newest accepted reading |
| window `readings` | list of `[epoch seconds, percent]` pairs, oldest first, at most 360 |

### Write rule

Several sessions can use the same writer name, for example one `orch-statusline-tap` per Claude Code session. An idle session can give an older value than a busy session. So each writer merges the new reading into its own file by these rules, per window:

1. Drop the reading when its `resets_at` is at or before now.
2. Start a new window object when the file has no valid window, when the stored `resets_at` is at or before now, or when `resets_at` moved forward by more than 600 seconds.
3. Drop the reading when `resets_at` moved back by more than 600 seconds, when the percent is lower than the stored percent, or when its time is earlier than the newest stored reading.
4. Else append the reading. When the reading before the newest one is less than 60 seconds older than the new reading, the new reading replaces the newest one. Keep the last 360 readings.

A dropped reading does not change `read_at`. A window that the input does not carry keeps its stored object. When no window changed, the writer does not write. A file that does not parse counts as empty, so the writer starts new windows.

A writer can write the file in place. `orch-statusline-tap` writes a temporary file in the same folder and renames it over its file, because Claude Code can stop a status line run in the middle of a write. When two sessions of one writer write at the same time, the last write wins and one reading can be lost.

### Reading rule and staleness

The reader lists `*.json` in the folder and skips a file when:

- it does not parse, or its `format` is not `basicly.usage-snapshot`;
- its `version` is not `1`;
- its `writer` field is not its file name;
- it has no `five_hour` window, or its `five_hour` `read_at` is older than 10 minutes (stale).

For each window, the reader takes the window object of the kept files with the latest `resets_at`, then the highest `used_percentage`, then the newest `read_at`. The burn rate uses the readings of that object.

`orch pace` refuses by name when the folder has no file, or when it skips every file. The refusal names each file and why it was skipped.

## 4. The status line tap

`orch-statusline-tap -- COMMAND [ARG...]` reads the status line input, starts COMMAND with the same input, writes the snapshot while COMMAND runs, and prints the output of COMMAND byte for byte. Its exit code is the exit code of COMMAND.

- Any error in the snapshot step is ignored. The output of COMMAND is still printed.
- The module imports only the Python standard library and the orch kit runner.
- A COMMAND that cannot start gives exit code 127 and a message on standard error.
- Measured 2026-11-26 on WSL with `~/.claude/statusline.sh` and a sample input built from the documentation (20 runs each): 23 to 24 ms direct, 49 to 52 ms through the tap, also with the settings command below. The tap adds 25 to 29 ms, mostly Python start-up.

Settings example:

```json
"statusLine": {
  "type": "command",
  "command": "if command -v orch-statusline-tap >/dev/null 2>&1; then exec orch-statusline-tap -- bash ~/.claude/statusline.sh; else exec bash ~/.claude/statusline.sh; fi",
  "padding": 0
}
```

The `command -v` test keeps the plain status line when `orch-statusline-tap` is not on `PATH`.

## 5. The plan: `orch pace`

`orch pace` prints, for each window, the used percent, the reset time, the burn rate, the projected percent at reset, the age of the reading and an advice word. Its last line is the overall advice.

- Burn rate of `five_hour`: `(last percent - first percent) / hours` over the readings of the last 60 minutes. It needs 2 readings that span at least 15 minutes.
- Else, and always for `seven_day`: the window average, `used percent / hours since the window start`, where the window start is `resets_at` minus the window length (5 hours or 7 days). It needs 30 minutes of a `five_hour` window and 24 hours of a `seven_day` window; else the rate is unknown and counts as 0. An early average covers only working hours and projects too high.
- Projected percent at reset: `used + rate x hours to resets_at`.
- Advice per window: `close idle` when the projection is 100% or more, or the used percent is 95% or more; `hold` when the projection is 90% or more; else `start`.
- A window whose `resets_at` has passed shows "window reset" and `start`.
- The overall advice is the most careful advice of the windows: `close idle`, then `hold`, then `start`.
- The `fit:` line names how many working sessions fit until the `five_hour` reset (record `basicly-scpjhge`). It takes the burn of the readings of the last 30 minutes, which must span at least 10 minutes, and divides it by the count of live sessions in the state `working`. The count is `(100 - used) / hours to reset / burn per session`, rounded down. With no such readings, no working session or no measured use, it prints `fit: unknown` and the reason.
- The `sessions:` line turns the fit into an action (record `basicly-zidiwg9`): `start N more` when more sessions fit than work, `hold at N` when they are equal, and `start none; let N of W finish` when fewer fit. It shows the needed rate `(100 - used) / hours to reset`, the burn per working session and both counts. When the `fit:` line is unknown (no working session, no measured burn, or a reset with no reading of the new window), it assumes a burn per session from `session_rate_pct_per_hour` in `pace.json` in the orch store and prints `assumed X%/h per session from config` with the reason. The default is 5%/h per session (source: the Orchestrator's measurement of 2026-11-26). After a reset with no new reading, the needed rate is 100% over the full 5 hours, 20%/h.

The words mean:

| Advice | The Orchestrator |
| --- | --- |
| `start` | can start more work in this window |
| `hold` | keeps the current load and starts nothing new |
| `close idle` | closes idle sessions and stops new work |

## 6. Cost per item

`orch pace note RECORD start` writes `[pace start] five_hour=P resets_at=T by=manual` on the record. `orch pace note RECORD done` writes `[pace done] five_hour=P resets_at=T change=C type=TYPE by=manual`. C is the percent change since the newest start note, or `unknown_crossed_reset` when a reset came between them. When an orchestrator store exists, both notes add `working_sessions=N` before `by=`, the count of live sessions in the state `working`.

The tools write the same notes by themselves:

| Moment | Caller | `by=` |
| --- | --- | --- |
| a vault claim lands | the tracker kit (`vault_cli.py`) | `claim` |
| the landing tool merges a lane | `basicly worktree merge` (`merge.py`) | `landing` |

Both call `orch_state/note.py` in the orch kit with `pace="start"` or `pace="done"`. The note is written only when all of these hold: an orchestrator store and a session id exist, the repository has `.basicly-tracker.toml` (vault mode, so the note never changes tracked ledger files), and a fresh snapshot exists. Each tracker call has a 15-second limit. Any error skips the note and never changes the result of the claim or the landing. A consumer without the engine gets the claim note; it writes the done note with `orch pace note RECORD done`.

`orch pace` ends with a table of cost per record type. It reads the records that hold a `[pace done]` note with one `list --query` call, and takes the newest `[pace done]` note with a `by=` field of each record. A note without `by=` is not counted; those are the sample notes of `basicly-qpr2xmr.3.39`. Columns: records, mean and maximum change in percent, and the count that crossed a reset. When the tracker cannot be read, the table line names the reason and the rest of the output stays.

The percent is for the whole account. C includes the usage of every session that worked at the same time. Read it with `working_sessions`.

## 7. Limits

- The share of cached tokens in plan usage is not documented, so the cost of an item must be measured.
- A writer clock that is wrong moves `read_at`.
- The mean cost per type does not correct for parallel sessions.
