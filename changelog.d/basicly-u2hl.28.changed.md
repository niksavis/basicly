- **The tier injection kit moves into its own directory.** `.basicly/core/kit/` now holds one
  directory per kit — `tier/` beside `tracker/` — instead of one foldered kit and three loose
  modules. **Breaking for an installed consumer**: a Claude settings hook written by
  `install_hook.py` names the old path and stops resolving until the installer is re-run from
  the new location, `python3 .basicly/core/kit/tier/install_hook.py --user`. The directory is
  not cosmetic — `kit-deployment` and `kit-boundary` scope themselves by it, so the three loose
  modules had no gate looking at them.
