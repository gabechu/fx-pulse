"""AUD/USD price-derived features (source: `oanda_ticks`).

Each submodule defines one feature family. Importing this package
triggers each submodule's registration side effects.

To add a family: drop a new file here and add one import line below.
To remove a family: delete the file and remove its import line.

Window units (used inside each submodule): all rolling windows are
expressed in *trading* 1-minute bars. Weekend bars are dropped by
`assemble._load_oanda_ticks`, so e.g. `_DAY = 1440` means ~1 trading day,
not 1 wall-clock day.
"""
from fx_pulse.features.price import (  # noqa: F401 — side-effect imports
    bollinger,
    log_return,
    pct_above_low,
    pct_below_high,
    return_stdev,
    rsi,
    zscore,
)
