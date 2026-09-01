- **The board's `running now` section now says when no pass is running.** A checkout with no
  live supervisor emitted no `lanes` section at all, so the panel read `not emitted by this
  producer` and could never say anything else. It now emits an empty section, which the page
  renders as `no lane is dispatched` (basicly-u6eeag).
