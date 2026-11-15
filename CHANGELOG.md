# Changelog

## 0.1.2 (unreleased)

- Docs link to EIA Open Data (API v2): the catalog documentation now ends with a
  "Sources and references" section linking the upstream service, its API
  documentation and where to get an API key (VGI183).
- The `main` schema comment is now a short one-line summary, so the schema's
  `vgi.doc_md` adds detail instead of repeating it (VGI102).
- The catalog's executable example now calls the worker instead of only
  querying DuckDB's own catalog (`duckdb_functions()`): `SELECT * FROM eia.main.routes(route := 'electricity') LIMIT 0` shows the columns without an API key or EIA request.

## 0.1.1 (unreleased)

- The catalog comment (the one-line description directories show, VGI001) is now
  a short plain-text sentence of at most 300 characters saying what data the
  worker covers. The long-form Markdown documentation remains in the catalog's
  `vgi.doc_md` tag and agent guidance in `vgi.doc_llm`.

## 0.1.0 (unreleased)

Initial worker generated from the VGI starter template.

Targets: vgi-python 0.51 · VGI protocol 3.3 · Haybarn 2.0.0rc11 · vgi-lint-check 2.x (unpinned)
