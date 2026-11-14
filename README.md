# vgi-eia

Query US Energy Information Administration data from DuckDB through a Python VGI
worker. Explore API v2 routes, frequencies and facets, then stream observations
or retrieve a legacy series ID through EIA's v2 translation endpoint.

## Install and query

Requires Python 3.13 or newer. Install a GitHub Release wheel, or run `make build`
in the locked source checkout. [Register for an EIA API key](https://www.eia.gov/opendata/)
and inject it as `EIA_API_KEY` in the worker environment or as a SQL secret.
Environment files are not loaded automatically. Never commit actual credentials.

```sql
SET allow_extension_repositories = 'allowed';
CREATE EXTENSION REPOSITORY IF NOT EXISTS query_farm
    WITH PREFIX 'https://duckdb-extensions.query.farm';
INSTALL vgi FROM query_farm;
LOAD vgi FROM query_farm;
ATTACH 'eia' (TYPE vgi, LOCATION '/absolute/path/to/.venv/bin/vgi-eia-worker');
CREATE SECRET eia_key (TYPE eia_api, API_KEY 'your-EIA-api-key');

SELECT route, name FROM eia.main.routes(route := 'electricity') ORDER BY route;
SELECT measure, units
FROM eia.main.measures('electricity/retail-sales') ORDER BY measure;

SELECT period, value, value_text, units
FROM eia.main.observations('electricity/retail-sales', 'price', 'monthly',
    facets_json := '{"stateid":["CO"],"sectorid":["RES"]}',
    start_period := '2024-01', end_period := '2024-12')
ORDER BY period;
```

A single applicable `eia_api` SQL secret takes precedence over `EIA_API_KEY`.
An invalid explicit key fails instead of falling back. Keys are never stored in
continuation state, and EIA's credential-echoing `request` object is discarded.
EIA's public `DEMO_KEY` can be used for limited exploration, but its shared quota
is unsuitable for a deployed service.

## Functions

All functions are in `eia.main`. SQL NULL arguments are rejected.

| Function | Purpose |
| --- | --- |
| `routes(route := '')` | Direct child routes; empty selects the root; leaf routes return zero children |
| `metadata(route)` | One row containing source notes, period bounds and complete metadata JSON |
| `frequencies(route)` | Accepted frequency identifiers and native period formats |
| `measures(route)` | Available numeric data columns, aliases and published units |
| `facets(route)` | Dimension identifiers accepted by observation filters |
| `facet_values(route, facet)` | Complete dimension-value IDs, names and aliases |
| `observations(route, measure, frequency, ...)` | One measure per source row at an explicit frequency |
| `series(series_id, ...)` | Legacy ID translated through `/v2/seriesid/`; frequency/measure inferred |

Routes are relative metadata paths such as `electricity/retail-sales`. Omit `/v2`,
`/data`, leading/trailing slashes and URL components. Absolute URLs, traversal and
query injection are rejected. Discover each route's supported parameters before
requesting observations; EIA datasets have different dimensions and frequencies.

```sql
SELECT frequency, period_format
FROM eia.main.frequencies('electricity/retail-sales') ORDER BY frequency;
SELECT id, name
FROM eia.main.facet_values('electricity/retail-sales', 'sectorid') ORDER BY id;

SELECT period, value, units
FROM eia.main.series('ELEC.SALES.CO-RES.A',
    start_period := '2022', end_period := '2024') ORDER BY period;
```

Observations and legacy series accept these named arguments:

| Argument | Default | Meaning |
| --- | --- | --- |
| `start_period`, `end_period` | `''` | Inclusive native EIA bounds; empty leaves the bound open |
| `page_size` | `1000` | Source rows per request, 1–5000 |
| `max_rows` | `0` | Explicit output cap up to 10 million; zero retrieves every match |
| `start_offset` | `0` | Zero-based source row offset |
| `sort_direction` | `'asc'` | `asc` or `desc`, applied to periods and dimension tie-breakers |

`observations()` additionally accepts `facets_json := '{}'`: an object mapping
facet names to nonempty arrays of distinct strings. String identifiers preserve
leading zeroes. One metadata request validates the measure, frequency and facets,
and obtains all facet IDs as sort tie-breakers. SQL WHERE clauses filter locally;
use arguments to reduce upstream work. Each call selects one numeric measure;
join calls explicitly when several measures are needed:

```sql
-- Colorado residential 2024 price weighted by monthly sales (14.92 cents/kWh
-- against a 14.88 simple average of the 12 monthly prices).
WITH price AS (
    SELECT period, value AS cents_per_kwh
    FROM eia.main.observations('electricity/retail-sales', 'price', 'monthly',
        facets_json := '{"stateid":["CO"],"sectorid":["RES"]}',
        start_period := '2024-01', end_period := '2024-12')),
sales AS (
    SELECT period, value AS million_kwh
    FROM eia.main.observations('electricity/retail-sales', 'sales', 'monthly',
        facets_json := '{"stateid":["CO"],"sectorid":["RES"]}',
        start_period := '2024-01', end_period := '2024-12'))
SELECT count(*) AS months,
    round(sum(price.cents_per_kwh * sales.million_kwh) / sum(sales.million_kwh), 2) AS weighted_price,
    round(avg(price.cents_per_kwh), 2) AS simple_average_price
FROM price JOIN sales USING (period) ORDER BY months;
```

Period bounds use the source's advertised format: years, months, quarters,
dates or ISO hours. The source validates dataset-specific date semantics. The
worker does not convert annual/quarterly/hourly periods into invented dates.

## Values and complete results

- `value` is DOUBLE in published units, without rescaling. `value_text` preserves
  source strings; older numeric JSON values use canonical numeric text. It is not
  a decimal precision guarantee for legacy JSON numbers.
- `value_status` distinguishes numeric, missing, withheld and insignificant.
  JSON null, blank strings, `null`, `NA`, `N/A` and `--` are missing; `w`/`W` are
  withheld and `*` is insignificant. Those become SQL NULL while their source
  marker remains available. Unknown or non-finite numeric values fail.
- `units` comes from the row's `<measure>-units` field. `dimensions_json` retains
  dimension IDs and labels; `source_json` retains the entire original row.
  Optional missing metadata returns NULL. Use ORDER BY for final SQL ordering.
- One validated source page becomes one Arrow batch. Source counts, repeated
  pages, duplicate identities and frequency changes are checked. A short/empty
  page before the advertised end fails, as does any upstream warning. Requests
  never exceed EIA's 5000-row page ceiling.
- An explicit output cap returns a prefix; SQL LIMIT alone is not an upstream
  cap. Separate calls are not an atomic snapshot, and equal-count concurrent
  revisions can escape detection. Prefer bounded historical period windows.
- Facet-value endpoints have no pagination in EIA's published OpenAPI schema.
  The worker checks `totalFacets` against the complete bounded response and fails
  on truncation or duplicates. Metadata discovery uses one bounded response.
- Legacy series must identify a single measure through its per-row units field.
  Ambiguous responses fail with guidance to use `observations()` explicitly.
  There is no API v1 client, bulk-file downloader, forecast-model-specific wrapper,
  persistent cache, or historical vintage reconstruction.

## HTTP authentication and deployment

```sh
export VGI_BEARER_TOKENS="$(openssl rand -hex 32)=analyst"
vgi-eia-worker --http --host 127.0.0.1 --port 8000
```

Store persistent tokens in a secret manager. Tokens contain 32–4096 characters;
multiple principals use `token1=principal1,token2=principal2`. Empty or malformed
configuration fails startup without exposing its contents. Omitting the variable
leaves HTTP unauthenticated for local development. Each principal can access the
whole catalog. `/health` remains public; terminate TLS at your service ingress.

```sql
INSTALL httpfs FROM core;
LOAD httpfs;
ATTACH 'eia' (TYPE vgi, LOCATION 'https://your-worker.example',
              bearer_token 'your-worker-bearer-token');
CREATE SECRET eia_key (TYPE eia_api, API_KEY 'your-EIA-api-key');
```

Worker bearer tokens and EIA keys have separate roles; bearer tokens never go to
EIA. A bearer-authenticated query still requires an upstream key. Worker errors
omit upstream bodies, request URLs and chained exceptions that could expose keys.
Arrow RPC owns stdout. `Dockerfile` runs as a nonroot user and serves HTTP on port
8000; `docker run -i vgi-eia stdio` selects stdio after building the image.

## Operations

| Environment variable | Default |
| --- | --- |
| `EIA_API_KEY` | unset; required unless supplied as a SQL secret |
| `EIA_TIMEOUT_SECONDS` | 30 (0.1–120) |
| `EIA_MAX_ATTEMPTS` | 4 (1–6) |
| `EIA_RETRY_BUDGET_SECONDS` | 120 (1–600) |
| `EIA_MIN_INTERVAL_SECONDS` | 0.5 |
| `EIA_MAX_RESPONSE_BYTES` | 16777216 (1 KiB–64 MiB) |
| `EIA_API_BASE_URL` | `https://api.eia.gov/v2` |
| `EIA_ALLOW_INSECURE_HTTP` | unset; `1` permits loopback fixture HTTP only |

Network failures, 429 and 5xx responses use bounded backoff with jitter.
`Retry-After` is honored; a delay beyond the budget fails without retrying early.
Redirects are refused. Spacing is process-local; coordinate account quotas across
replicas. The read budget is checked between chunks; blocking socket operations
can extend wall time by their timeout. API base URLs are trusted operator settings.
TLS certificate verification stays enabled; `SSL_CERT_FILE` can select your
approved CA bundle when the environment needs a custom trust store.

## Verification and releases

```sh
make check          # Ruff, strict mypy, pydoclint, pytest, SQL transports, metadata, stock DuckDB
make smoke-live     # Official API using EIA_API_KEY; consumes its quota
make package
uvx twine check dist/*
python3 ci/verify-artifacts.py --python 3.13
make lint-ai        # Optional external-agent metadata evaluation
```

Offline fixtures drive all required checks, including metadata behavior at strict
`fail_on = "info"` with no waivers. Wheel and source installs repeat stock DuckDB
and authenticated HTTP contracts outside the checkout. See
[VERIFICATION.md](VERIFICATION.md) for measured results and live-check limitations.
CI gates releases, signs both distributions with Sigstore, and attests wheel
provenance. Source data terms remain separate from the worker's 0BSD license.

Sources: [EIA API technical documentation](https://www.eia.gov/opendata/documentation.php),
[EIA OpenAPI download](https://www.eia.gov/opendata/eia-api-swagger.zip),
[EIA API FAQ](https://www.eia.gov/opendata/faqs.php),
[legacy value markers](https://www.eia.gov/opendata/v1/commands.php).

## Executable notebooks

[Weight monthly electricity prices by sales](notebooks/README.md) uses recorded official EIA responses, visible SQL and a controlled half-year/full-year comparison.
