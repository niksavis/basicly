- **A run can now choose the model tier it dispatches at, without editing committed
  config.** `basicly loop supervise` took `--runner` and `--autonomy` and no way to name a
  capability tier, so dispatching at `maximum` — `claude-fable-5` on Anthropic — meant
  editing `[runner] default_tier`. The session-override module's own reasoning rules that
  out: editing the committed file changes behaviour for every consumer, while the whole
  point of the registry is that configuring one run should be one command rather than a
  config edit plus a revert the operator has to remember.

  `--tier {low,medium,high,maximum}` joins the other two on the same mechanism and in the
  same shared helper, so it reaches every subcommand that can dispatch an agent. It is
  validated against the known tiers **before** anything is applied, on the all-or-nothing
  rule the existing pair already follows, and it lands in every run record for free
  because the record builder stamps the active overrides centrally — an unrecorded
  override would leave two genuinely different dispatches behind indistinguishable
  records.

  It selects the tier for the **whole pass**, not per lane: runner selection resolves one
  spec per round. Two models can still appear on one board at the same time, because a
  lane card reads its model from that lane's own last run record rather than from the
  current pass — so a lane whose previous dispatch ran on one model renders it beside a
  lane dispatched now on another. Per-lane selection is a separate, unbuilt piece of work.
