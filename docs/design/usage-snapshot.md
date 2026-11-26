# Usage snapshot

Status: version 1, 2026-11-26. Tracker: `basicly-qpr2xmr.3.39`.

## 1. Problem

The Orchestrator plans the work of each 5-hour Claude usage window. Usage must end near 100% at the reset and must not reach the limit early.
Claude Code gives the used percent of the 5-hour and 7-day windows only on the standard input of the status line command.
No hook, OpenTelemetry metric or session channel gives these values (Claude Code 2.1.332 documentation, read 2026-11-26).

## 2. Source of the values

- The status line input carries `rate_limits.five_hour` and `rate_limits.seven_day`. Each has `used_percentage` (0 to 100) and `resets_at` (Unix epoch seconds) (code.claude.com/docs/en/statusline, read 2026-11-26).
- `rate_limits` is present only for claude.ai Pro, Max and Ultra subscribers, and only after the first API response of the session. Each window can be absent. Claude Code drops a window when its `resets_at` time passes.
- The status line runs at session start, at each new assistant message, after `/compact`, and on the other events that the documentation names. Claude Code debounces updates at 300 ms and cancels a run that is still in progress when a new update starts.
- Without `refreshInterval`, an idle session does not run the status line again. An idle session also does not spend usage, so its last value stays correct.

## 3. The file

| Item | Value |
| --- | --- |
| Path | `BASICLY_USAGE_SNAPSHOT` when set; else `$XDG_STATE_HOME/basicly/usage-snapshot.json` (an absolute `XDG_STATE_HOME` only, else `~/.local/state`); on Windows `%LOCALAPPDATA%\\basicly\\usage-snapshot.json` |
| Owner | basicly. Other writers write the same format and read nothing of basicly |
| Writers | `orch-statusline-tap` (basicly-orch package), `handily-board` |
| Format | JSON, UTF-8 |

One file holds the readings of all sessions of one user. A writer computes the path itself. It needs no basicly code.

### Fields

| Field | Unit and source |
| --- | --- |
| `format` | always `basicly.usage-snapshot` |
| `version` | integer, `1` |
| `writer` | name of the program that wrote the file |
| `written_at` | Unix epoch seconds of the write |
| `windows.five_hour`, `windows.seven_day` | one window object each; a window can be absent |
| window `used_percentage` | number, 0 to 100, from `rate_limits.<window>.used_percentage` |
| window `resets_at` | integer, Unix epoch seconds, from `rate_limits.<window>.resets_at`. A writer that gets ISO text converts it |
| window `read_at` | integer, Unix epoch seconds of the newest accepted reading |
| window `readings` | list of `[epoch seconds, percent]` pairs, oldest first, at most 360 |

### Write rule

Many sessions write the same file. An idle session can give an older value than a busy session. So each writer merges by these rules, per window:

1. Drop the reading when its `resets_at` is at or before now.
2. Start a new window object when the file has no valid window, when the stored `resets_at` is at or before now, or when `resets_at` moved forward by more than 600 seconds.
3. Drop the reading when `resets_at` moved back by more than 600 seconds, when the percent is lower than the stored percent, or when its time is earlier than the newest stored reading.
4. Else append the reading. When the reading before the newest one is less than 60 seconds older than the new reading, the new reading replaces the newest one. Keep the last 360 readings.

A dropped reading does not change `read_at`. A window that the input does not carry keeps its stored object. When no window changed, the writer does not write.

The writer writes a temporary file in the same folder and renames it over the path. A reader never sees a half-written file. When two writers write at the same time, the last rename wins and one reading can be lost.

A reader ignores a file with another `format` or `version`. `orch pace` refuses it by name.

### Staleness

A snapshot is stale when the newest `five_hour` reading (`read_at`) is older than 10 minutes. `orch pace` refuses a missing, unreadable, foreign or stale snapshot and names the setup step.

## 4. The status line tap

`orch-statusline-tap -- COMMAND [ARG...]` reads the status line input, starts COMMAND with the same input, writes the snapshot while COMMAND runs, and prints the output of COMMAND byte for byte. Its exit code is the exit code of COMMAND.

- Any error in the snapshot step is ignored. The output of COMMAND is still printed.
- The module imports only the Python standard library and the orch kit runner.
- A COMMAND that cannot start gives exit code 127 and a message on standard error.
- Measured 2026-11-26 on WSL with `~/.claude/statusline.sh` and a sample input built from the documentation (20 runs each): 24 ms direct, 49 ms through the tap. The tap adds about 25 ms, mostly Python start-up.

Settings example:

```json
"statusLine": {"type": "command", "command": "orch-statusline-tap -- bash ~/.claude/statusline.sh"}
```

If `orch-statusline-tap` is not on `PATH`, the shell cannot start it and the status line is empty.

## 5. The plan: `orch pace`

`orch pace` prints, for each window, the used percent, the reset time, the burn rate, the projected percent at reset, the age of the reading and an advice word. Its last line is the overall advice.

- Burn rate of `five_hour`: `(last percent - first percent) / hours` over the readings of the last 60 minutes. It needs 2 readings that span at least 15 minutes.
- Else, and always for `seven_day`: the window average, `used percent / hours since the window start`, where the window start is `resets_at` minus the window length (5 hours or 7 days). It needs 10 minutes of window; else the rate is unknown and counts as 0.
- Projected percent at reset: `used + rate x hours to resets_at`.
- Advice per window: `close idle` when the projection is 100% or more, or the used percent is 95% or more; `hold` when the projection is 90% or more; else `start`.
- A window whose `resets_at` has passed shows "window reset" and `start`.
- The overall advice is the most careful advice of the windows: `close idle`, then `hold`, then `start`.

The words mean:

| Advice | The Orchestrator |
| --- | --- |
| `start` | can start more work in this window |
| `hold` | keeps the current load and starts nothing new |
| `close idle` | closes idle sessions and stops new work |

## 6. Cost per item

`orch pace note RECORD start` writes `[pace start] five_hour=P resets_at=T` on the record. `orch pace note RECORD done` writes `[pace done] five_hour=P resets_at=T change=C type=TYPE`. C is the percent change since the newest start note, or `unknown_crossed_reset` when a reset came between them. When an orchestrator store exists, both notes add `working_sessions=N`.

The percent is for the whole account. C includes the usage of every session that worked at the same time. Read it with `working_sessions`.

## 7. Limits

- The share of cached tokens in plan usage is not documented, so the cost of an item must be measured.
- A writer clock that is wrong moves `read_at`.
