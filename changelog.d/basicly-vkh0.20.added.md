- **The owned scheduler ranks the ready set with a pure score that reads no clock.** A repo
  on `[tracker] mode = "owned"` now takes its dispatch order from the kit's
  `tracker/scheduler.py` instead of `br scheduler`, through `br.read_ranking` — the
  ranking's own seam, the shape `br.read_record` already has for a record. The ordering is
  `priority ASC, dependents DESC, id ASC` over the ready set: `br`'s fallback policy sorted
  by `created_at`, which made dispatch order depend on when a ledger happened to be written
  rather than on the graph. Nothing on the ranking's input carries a timestamp, so the same
  work graph ranks identically however long it has been sitting there (`basicly-vkh0.20`).

  The score is one integer holding both terms, and `scheduler.explain()` decodes it back
  into "P0, three dependents" — so a dispatch marker recorded months ago stays readable
  without the graph it was computed over. Each answer names the policy that produced it
  (`schema: basicly.scheduler.v1`), which is what tells a rank recorded under the owned
  scorer from one recorded under `br.scheduler.v1`.

  Two things a consumer will notice if they flip. The dependent count is over **blocking
  edges to still-live dependents** only, so a `related` dependent and a closed one both
  count for nothing. And the owned ranking has an opinion where `br` had none: `br scheduler`
  recommends only unclaimed work, while this ranks every ready record, `in_progress`
  included. Repos on `external` or `dual` are unaffected — `br scheduler` still answers, with
  its own schema and sort recorded exactly as before.
