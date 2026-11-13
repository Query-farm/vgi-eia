# Release verification

Verified 2026-11-13 on macOS arm64 with Python 3.14.4. Installed artifact checks
also run with Python 3.13.16.

- Starter: Query-farm/vgi-starter-python at `9cca9105474d6e0052b8e343c8ea21f3d1a7503c`.
  Baseline passed before adaptation; original sample assertions remain in the suite.
- Runtime: vgi-python 0.51.1 / vgi-rpc 0.53.1; starter SDK constraints retained.
- `make check`: Ruff formatting/lint, strict mypy, pydoclint, 107 pytest tests;
  Haybarn 2.0.0rc11: 141 stdio assertions, 141 launch, 105 HTTP, zero skips.
- Metadata: 100/100 L2, zero findings, strict `fail_on = "info"`, no waivers;
  behavioral probes run against deterministic fixtures.
- Stock DuckDB 2.0.0 / signed Query.Farm VGI extension `e75482339d`:
  21 stdio and 24 HTTP checks, covering all eight functions, paginated source rows,
  missing markers, legacy numeric representations, bounds, offsets, source filters,
  bearer rejection and a separately required upstream secret.
- Wheel and source distribution: metadata validation and isolated installations;
  each repeats the 21 stdio and 24 authenticated HTTP checks outside this checkout.

## Live API evidence and limits

Six stock-DuckDB checks passed against the official EIA API using its public
`DEMO_KEY`: routes, metadata, frequencies, measures, facets and facet values.
The shared demo quota then returned HTTP 429 with a Retry-After of 61231 seconds,
before the paginated observation and legacy-series SQL checks could complete.
The worker refused to retry sooner than that limit. Therefore the complete
`make smoke-live` suite is **not claimed as passed**. An operator-owned EIA key
is needed to complete those remaining live data checks.

Separately, direct official API inspection confirmed the v2 legacy-series response
shape (including numeric periods/values) and complete facet listings. Offline
worker/SQL contracts cover those shapes and pagination failures. The API reported
version 2.1.14 at inspection time. Local live calls used the machine's approved CA
bundle via `SSL_CERT_FILE`; certificate verification remained enabled.

Reports are generated in ignored `build/`. CI runs quality checks on Python
3.13/3.14, Linux/Windows SQL transports, stock DuckDB, installed artifacts and
Docker health checks. Releases are CI-gated, wheel and source assets are
Sigstore-signed, and the wheel carries a provenance attestation.

Queries are not atomic snapshots across pages. Source total checks cannot detect
all equal-count concurrent revisions. Facet listings fail on truncation because
EIA's published OpenAPI does not specify pagination for those endpoints.

## Optional AI review

The optional L3 evaluation could not complete because its external agent backend
rejected the evaluation prompt; L3 assurance is not claimed. Its documentation
review also reported an observation-argument order mismatch. Direct stock-DuckDB
inspection shows `duckdb_functions().parameters` and `parameter_types` align
correctly, and the SQL suite exercises the named arguments successfully. The
review appears to compare alphabetically sorted argument descriptors with the
engine's unsorted parameter types. Documentation now explicitly identifies the
three required positional arguments and the remaining named arguments. Required
static/behavioral lint has zero findings; no lint rules were waived.
