- **A relation stated by two edge events is one row, not two.** `tracker show` counted an
  imported duplicate twice - nine such parent-child relations sit in this repo's log. The
  fold has answered one row since v0.10.0, as a side effect of edge retraction rather than
  by design; the guarantee is now stated where the fold lives and tested over the committed
  ledger (basicly-vkh0.52).
