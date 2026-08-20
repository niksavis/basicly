A `pipe-status-guard` PreToolUse hook refuses reading a pipeline's exit status when a
pass-through filter ends it. `cmd | tail` exits with tail's status, so a failing gate
reports success; the rule was already always-on prose and the trap still fired twice on
2026-08-20, plus once as a background run notifying "exit code 0" over a failed gate.
`head` and `tail` are this repo's 1st and 3rd most-used tools at 16394 and 13960
invocations, so the guard fires only where the status is actually *read* — `$?`, `&&`/`||`
chaining, an `if`/`while`/`until` condition, or a backgrounded call where the reported
code is the pipeline's. It stays silent on the idiom, on a `grep -q` assertion, and when
`set -o pipefail` or `PIPESTATUS` shows the caller already handled it. The refusal names
the redirect-to-a-file form rather than saying be careful.

`falsify-first` gains the rule that a probe must exclude the file defining its own
vocabulary: the instrument is not a member of its own population, so an agreement
between the two is a derived route and not a second derivation.
