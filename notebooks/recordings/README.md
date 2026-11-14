# Official EIA API v2 recordings

These five responses were retrieved on 14 November 2026 UTC using EIA's public
DEMO_KEY: retail-sales metadata, price pages at offsets 0 and 6, and sales pages
at offsets 0 and 6. Each data page requests six rows for Colorado (CO), residential
(RES), monthly frequency, January–December 2024 inclusive. Explicit sort keys are
period, stateid and sectorid, all ascending.

The top-level `request` object, which echoes credentials, was removed. JSON was
then reformatted. **All source `response` data is unchanged.** The manifest records
both original-response and saved-file SHA-256 hashes, exact noncredential request
parameters, original endpoint URLs and UTC retrieval timestamps. No API key is
stored in these committed files. These are public source recordings, not synthetic
observations or a guarantee of historical vintage accuracy.

The loopback server validates saved hashes and matches exact paths/parameters;
it serves each captured page without filtering, repagination or calculations.
An explicit dummy credential is required locally. The actual published EIA worker
handles source metadata, paging, units, markers and Arrow output.

Source: U.S. Energy Information Administration, https://www.eia.gov/opendata/ .
Attribution does not imply EIA endorsement of this notebook's calculations.
