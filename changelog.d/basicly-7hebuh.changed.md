- **A grant's spend is now read from the committed ledger as well as the local run-record
  file**, so a clone reports the same `spent` as the machine that ran the dispatches instead
  of `unknown`. Each store's own figure is printed where the two differ. The D3 ceiling is
  unchanged and still meters the local file (basicly-7hebuh).
