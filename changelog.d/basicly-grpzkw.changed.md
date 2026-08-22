- **The always-on instruction layer now says what counts as *authority* when checking whether
  a capability already exists.** Its reuse rule said *"grep for the helper, skill or gate
  before proposing one; absence needs a probe"* — and an agent followed it, grepped the
  config file, read `--help`, and concluded a feature was missing that had shipped long ago.
  The key it needed is read by the config loader and appears nowhere in the config file, so
  every honest reading of the documented surface said "absent".

  The rule now reads: **prove a capability absent before building it; the authority is the
  code that reads it, not the docs or `--help`.** A live key can be undocumented, and a
  missing flag is not a missing feature.

  It was added by **removing**, not appending — the layer had eight characters of headroom on
  its tightest surface, and the tightest surface binds for anything always-on. The retired
  fragment was a generic retrieval ladder: *"find files by name, localize with focused
  search, read only the ranges you need."* That is agent hygiene rather than repo knowledge,
  and reading the narrow range and stopping is precisely the habit that hid the key. Retired
  rather than deleted, so the reason stays on the record.

  Net effect on every projected surface is smaller, not larger: headroom rose from 107 to
  224, 209 to 326, and 8 to 125 characters.
