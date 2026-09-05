- **The board now draws a form for a real pending checkpoint.** It read `asks[].actions[]` to
  know which verb answers an ask, and this producer never wrote that key — so the region
  worked only against hand-authored fixtures. `checkpoint` now offers `checkpoint-approve` and
  `decision` offers `loop-answer`; any other kind carries none (basicly-3qstvw).
