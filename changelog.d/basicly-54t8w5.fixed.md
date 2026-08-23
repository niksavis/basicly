- `--allow-retry` no longer degrades an L3 session to L2: the session-wide escalation
  scan now counts charged rework (attempts minus granted allowances), matching every
  other consumer of the cap.
