# EIA notebooks

[01 · Weight monthly electricity prices by sales](01_weight_monthly_electricity_prices.ipynb)
compares equal-month and sales-weighted means for Colorado residential prices in
2024. Visible SQL aligns independently retrieved measures, preserves availability
statuses and decomposes the difference. Two charts show prices/sales and signed
monthly contributions. Default data is recorded official EIA API output.

## Install and run

Install [uv](https://docs.astral.sh/uv/) and Git, then run from this directory:

```sh
uv sync --locked
uv run --locked python execute.py --timeout 600
```

The directory runs independently of the worker checkout. Its lock pins Haybarn
**2.0.0rc11**, JupySQL, plotting/execution dependencies and a trusted CA bundle.
The runner uses a temporary kernel with its interpreter and this directory as cwd;
it atomically saves only a successful execution. Overrides require a different
output directory. For an existing Jupyter frontend, optionally register a kernel:

```sh
uv run --locked python -m ipykernel install --user \
  --name vgi-eia-notebooks --display-name "Python (vgi-eia notebooks)"
```

Restart and run all after edits. The runner registers no permanent kernel. First
execution needs package and extension downloads.

## Literal remote worker

The visible SQL attaches using:

```text
uvx --python 3.13 --with vgi-python==0.51.1 --with vgi-rpc==0.53.1 --with pyarrow==26.0.1 --from git+https://github.com/Query-farm/vgi-eia@v0.1.0 vgi-eia-worker
```

The annotated tag resolves to **97dcb66719dba29e459fa1afaebf322bee9bfabb**. SDK,
RPC and Arrow pins match the tested worker lock. uv installs the remote package
in its own cache; no parent source, local binary or root environment is required.
The root lock is unchanged. The community VGI extension is downloaded separately
from the notebook environment lock.

## Recorded and live modes

`DATA_MODE = "recorded"` serves five official API responses captured on
**14 November 2026 UTC** via the worker's supported loopback `EIA_API_BASE_URL`
and explicit HTTP-test opt-in. The server checks hashes and exact request
parameters; it performs no filtering, repagination, numeric parsing or analysis.
Only the credential-echoing top-level `request` object was removed from captures,
and JSON was reformatted. All source response data is unchanged. See the
[recording guide](recordings/README.md) and [manifest](recordings/manifest.json).
A dummy local credential replaces any inherited upstream key during recorded runs.

```sh
uv run --locked python execute.py --timeout 600 \
  --parameters '{"DATA_MODE": "live"}' --output-dir artifacts/live
```

Live removes inherited endpoint/test settings and requests the **same historical
2024 window** from official HTTPS. It uses `EIA_API_KEY` from the environment or,
if absent, EIA's public `DEMO_KEY`. Keep operator credentials outside notebooks and
saved outputs. The shared demo quota is limited; an invalid explicit key does not
fall back. Environment files are not loaded automatically. TLS verification stays
on, using an existing `SSL_CERT_FILE` or the pinned certifi bundle.

Requests are spaced by 0.6 seconds, with one attempt per call, a 60-second timeout
and a 70-second request budget. Live failures remain failures; the runner preserves
the successful default. During recording, initial price and sales requests timed
out; bounded subsequent attempts obtained all four pages. No values were invented.

## Window, sample and units

State/sector are fixed **CO / RES**. Both modes fetch January–December 2024 in
six-row source pages for each measure, with `max_rows = 0` (all matching rows).
`END_MONTH` selects January through that month **inclusive** for local analysis;
accepted range **1–12**, default **6**. Native `YYYY-MM` labels remain strings,
without assigning a timezone or observation day. Retrieval clocks are UTC.

Join keys are period, stateid and sectorid. Missing source rows, missing/withheld/
insignificant markers, numeric zero and numeric values stay distinct. Both means
use the same usable paired months; zero sales gives zero weight. A window with
no usable pair or no positive total sales stops. Unexpected units stop rather than
being combined. `source_json` and `value_text` preserve the source row and marker.

Price is **cents per kilowatt-hour**; sales is **million kilowatt hours**. The million
factor cancels in normalized sales weights. Equal-month mean gives every paired
month weight 1/n; sales-weighted mean gives it sales/total sales. The contribution
`(sales_weight − 1/n) × (price − equal_month_mean)` sums to their difference.
Double-precision identities use absolute tolerance 1e-10 cents/kWh.

These are averages of reported monthly prices, which are already rounded. They
are not a recomputation of an official annual price from unrounded revenue/sales,
a household bill, causal estimate or forecast. Separate measures/pages are not
atomic: equal-count concurrent source revisions can escape pagination checks.
Current-source historical data is not a vintage reconstruction.

## Focused experiment

```sh
uv run --locked python execute.py --timeout 600 \
  --parameters '{"END_MONTH": 12}' --output-dir artifacts/full-year
```

Change only **END_MONTH, 6 → 12**, in recorded mode. Predict increased sample
coverage/total sales when additional usable months exist, but do not assume the
price difference must move in either direction. Compare both means, paired-month
count and signed contribution bars. Reset to **6**. Full-year fetched data stays
fixed; only the local selection changes.

The price panel explicitly uses a zoomed axis; the sales panel starts at zero.
Contribution charts share a symmetric signed scale within each run, with limits
adapted to the selected sample. Compare numeric labels across runs. The runner
copies helpers, guide, recordings, lock and unchanged branding beside experiment
outputs. Generated artifacts are ignored; the primary notebook saves its outputs.

## Validation and attribution

Nine SQL invariants check identity, units, matched samples and weight/contribution
identities. Recorded runs also audit exactly three metadata and four data-page
requests, proving DataFrame captures do not refetch. Default and a detached
full-year experiment are executed, charts inspected, and three preservation guards
check runner failure handling. The direct live notebook reached the official API but failed on a data page with
**HTTP 429 (shared public DEMO_KEY rate limit)**. Full live execution is **not claimed
as passed**. The earlier direct official captures obtained all four data pages;
recorded validation remains reproducible without upstream access. No automatic
retry was made after the quota response.

Repository `make check` covers formatting/lint, strict mypy, pydoclint, 107 pytest
cases, 141 stdio/141 launch/105 HTTP SQL assertions, metadata 100/100 L2 with zero
findings, and stock DuckDB 21 stdio/24 authenticated HTTP checks.

Source: U.S. Energy Information Administration. See [EIA Open Data](https://www.eia.gov/opendata/).
No endorsement is implied. To upgrade, change the literal remote pin, verify SDK
compatibility, rerun both windows and the live path, and inspect charts before
replacing outputs. New recordings need explicit provenance and checksums.
