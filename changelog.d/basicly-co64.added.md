- **`.scripts/headroom.py` reports both size ratchets in one command.** Measuring headroom
  took two commands, so an agent measured one and paid for the other — three times in one
  session. The report names every module's token headroom and its prose-share headroom side
  by side and ends with how many of the tree's modules sit close to a bound. Measured on this
  tree at the time it landed: 163 of 433 modules within 615 tokens or 1.0 point of a bound,
  20 at zero token headroom and 46 at zero prose headroom (`basicly-co64`).
