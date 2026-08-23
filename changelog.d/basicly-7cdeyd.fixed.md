- A lane waiting for a process-budget slot is no longer flagged "may be stuck": the slot
  is granted before the stall watchdog starts, so the wait it measures is real work time.
