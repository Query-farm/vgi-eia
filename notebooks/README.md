# EIA Energy Data notebooks

| Notebook | Question and data | Experiment |
| --- | --- | --- |
| [01 · Weight monthly electricity prices by sales](01_weight_monthly_electricity_prices.ipynb) | Discover the retail-sales contract, then compare equal-month and sales-weighted prices on the same Colorado residential months. Six **recorded official EIA responses** supply metadata, discovery and full-year 2024 observations. | `END_MONTH`: 6 → 12 |
| [02 · A numeric zero still contributes to observed coverage](02_zero_is_an_observation.ipynb) | Resolve a sector identifier and reconcile legacy-series versus explicit-route requests. Six **authored synthetic** observations distinguish numeric zero from withheld, missing and insignificant markers before applying a display requirement. | `MIN_OBSERVED_MONTHS`: 3 → 4 |

Each example is independent. SQL materializes worker reads before DataFrame capture;
all analysis and invariant checks remain visible. Four SVG figures show the weighting
arithmetic and the separate quantity/coverage questions.

## Run locally or in Colab

From this directory, with [uv](https://docs.astral.sh/uv/) and Git installed:

```sh
uv sync --locked
uv run --locked vgi-notebooks --timeout 600
uv run --locked vgi-notebooks 01_weight_monthly_electricity_prices.ipynb \
  --parameters '{"END_MONTH":12}' --output-dir artifacts/full-year
uv run --locked vgi-notebooks 02_zero_is_an_observation.ipynb \
  --parameters '{"MIN_OBSERVED_MONTHS":4}' --output-dir artifacts/minimum-four
```

The lock pins the local runner and Haybarn **2.0.0rc11**. The shared
[notebook package v0.3.35](https://github.com/Query-farm/vgi-example-notebooks/tree/v0.3.35)
provides setup, credential isolation, presentation and execution. Its Colab bootstrap
installs only the base package, preserving compatible preloaded plotting/Jupyter
libraries. Open-in-Colab links are at the top of each notebook. Local Python 3.12
preloaded-kernel compatibility was exercised; a hosted Colab session is not claimed.

The `eia` companion registry retrieves and checks the two helpers and fourteen JSON
files from immutable commit `42da23f8670be01908bf8df94152349295944ff4`. A standalone
notebook needs no cloned worker, local source environment or manual fixture copy.
The two files remain independent; restart the kernel and run all when switching
between their replay sources. Experiment outputs go to a separate directory and
retain native SVG output. Published defaults also link content-addressed SVGs for
GitHub's notebook renderer.

## Released worker and SQL credentials

Both notebooks visibly attach:

```text
uvx --python 3.13 --from git+https://github.com/Query-farm/vgi-eia@v0.1.0 vgi-eia-worker
```

The tag resolves to `97dcb66719dba29e459fa1afaebf322bee9bfabb`. The worker's declared
dependencies install in its isolated process, without transitive override flags or
changes to the notebook kernel. No parent source or worker build is required.

Before ATTACH, shared setup creates Haybarn with a fresh empty temporary
`secret_directory` and `allow_persistent_secrets=False`. Its empty inventory is
checked before connection use and again at cleanup. Neither notebook creates a
SQL secret. Replays replace inherited credentials with a fixed local dummy key
and explicitly allow only their loopback HTTP fixture endpoint. Cleanup closes the
connection and server, removes the temporary secret directory and restores the
previous environment without displaying its values.

## Recorded, synthetic and optional live data

Notebook 01 defaults to `DATA_MODE="recorded"`. The five original metadata/price/
sales recordings and the added electricity-parent-route response were captured
from official EIA API v2 on **14 November 2026 UTC**. Exact source URLs without
credentials, capture times and hashes appear in the
[recording manifest](recordings/manifest.json). Only the credential-echoing top-level
`request` object was removed and JSON reformatted; source data is unchanged.
The server verifies bytes and exact noncredential query arguments. It does not
filter, repaginate, parse numeric markers or perform analysis; the actual worker does.

Notebook 02 always uses the separate [synthetic fixture set](marker_fixtures/README.md).
The six authored months contain `0`, `12.5`, `w`, `null`, `*`, `18`. These are not
measured Colorado energy data. The real worker returns three numeric values and
three distinct nonnumeric statuses through both API access paths. The 30.5 million
kWh available-month subtotal is never presented as a complete six-month total.
The chosen completeness requirement is an example policy, not an EIA publication rule.

Optional live use applies only to notebook 01:

```sh
uv run --locked vgi-notebooks 01_weight_monthly_electricity_prices.ipynb \
  --parameters '{"DATA_MODE":"live"}' --output-dir artifacts/live --timeout 600
```

Live resets endpoint/test overrides, uses verified official HTTPS and **only EIA's
public `DEMO_KEY`**, ignoring inherited private keys. No private key is needed or
saved. Requests use one attempt, a 60-second timeout, a 70-second budget and at least
0.6 seconds between calls. An upstream error remains an error; there is no data-mode
fallback. The earlier full live execution failed with **HTTP 429** on a data page.
During this revision, the new official parent-route capture succeeded, then a facet
request returned **HTTP 429**; collection stopped without retries. Full live
execution is not claimed as passed. Recorded and explicitly synthetic defaults
remain reproducible without the upstream API.

## Interpretation and experiments

Notebook 01 fetches January–December 2024 in six-row pages for each measure, then
selects January through `END_MONTH` locally. Both means use the same usable paired
months. Price units are cents/kWh; sales units are million kWh, whose scale factor
cancels in normalized weights. Native `YYYY-MM` strings carry no invented timezone.
The first plot separates a labeled zoomed price axis from a zero-based sales axis.
The second decomposes the gap as `(sales_weight − 1/n) × (price − equal_month_mean)`.

With six months, equal-month and sales-weighted prices are **14.5133** and **14.5251**
cents/kWh; with twelve they are **14.8800** and **14.9169**. The gap changes from
**+0.0118** to **+0.0369** cents/kWh while all 24 fetched rows stay fixed. These are
averages of already-rounded monthly prices, not a reconstruction of an official
annual price from unrounded revenue and sales, a bill, a causal effect or a forecast.
Independent pages/measures are not atomic; equal-count revisions may evade checks.
Capture date is not a historical vintage.

Notebook 02 keeps all six authored months fixed. Raising the required numeric count
from **3 to 4** leaves the observed count **3**, including the zero, and the available
subtotal **30.5**. Eligibility changes from true to false and the displayed subtotal
becomes NULL. The quantity/status chart is identical; the coverage chart moves only
the requirement and its resulting interpretation. No unknown value is imputed.

## Coverage and validation

[coverage.json](coverage.json) maps meaningful visible SQL, returned results,
interpretations and successful execution receipts to **8/8 distinct names** in
both the released and current catalogs. Full functions/tables/views exports are
retained; authentication requirements exclude nothing. The release is executed;
the current public revision `a7d8d8b9bbc069bdaa03a939fcb66d771105f632` is separately
cataloged and has the same eight names. Exact current-runtime analytical execution
is not claimed (released/current exact counts: 8/0).

Both defaults, detached experiments and fresh notebook-only preloaded runs passed.
Nine SQL checks plus discovery assertions validate notebook 01; eight SQL checks
reconcile notebook 02. Replay audits verify exactly eleven and six requests,
respectively, with no refetch from captures. Default-preservation/parameter guards,
Ruff, notebook presentation checks and all changed figures were reviewed. Unchanged
worker source reuses its [green source CI](https://github.com/Query-farm/vgi-eia/actions/runs/43614836691);
no worker rebuild, source-suite rerun or container publication was needed.

Source: [U.S. Energy Information Administration API documentation](https://www.eia.gov/opendata/documentation.php).
No endorsement is implied. Updating a worker or companion pin requires renewed
catalog, default/experiment, request-audit and visual validation.
