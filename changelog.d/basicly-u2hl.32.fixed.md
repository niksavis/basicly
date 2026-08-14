- **The `chars/4` token estimator is constrained to prose, and the scope reader stops parsing
  binaries as text.** The estimate is calibrated on English and is wrong by a wide margin on
  anything else, which fed a sizing governor that decides whether a unit of work earns a lane at
  all. It now applies where it is valid and reports rather than guessing where it is not.

  The tokenizer itself deliberately stays `chars/4`: a real one fetches a 3.5 MB vocabulary over
  HTTPS on first use, which a consumer's git hook cannot do. That is a decision with its error band
  recorded rather than an unmeasured default (`basicly-u2hl.32`, `basicly-ca42`).
