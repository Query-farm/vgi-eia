"""EIA credentials, safe API routes, filters and response validation."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import pyarrow as pa
from vgi.catalog import SecretTypeSpec
from vgi.table_function import ProcessParams

from .transport import ApiError, Config, request_json

SECRET_TYPE = "eia_api"
API_SECRET_TYPES = [
    SecretTypeSpec(
        name=SECRET_TYPE,
        description="EIA Open Data API key; register at https://www.eia.gov/opendata/.",
        schema=pa.schema(
            [pa.field("api_key", pa.string(), metadata={"redact": "true", "vgi_doc": "Your EIA Open Data API key."})],
            metadata={"vgi_doc_url": "https://www.eia.gov/opendata/"},
        ),
    )
]
ROUTE_PATTERN = r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$"
FIELD_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"
SERIES_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$"


def validate_key(value: str | None) -> str:
    """Reject absent or malformed credentials without disclosing their contents."""
    if not value:
        raise ApiError("EIA API key required: create an eia_api secret or set EIA_API_KEY")
    if re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value) is None:
        raise ApiError("EIA API key has an invalid format")
    return value


def credential(params: ProcessParams[Any]) -> str:
    """Resolve a SQL secret before the worker environment."""
    secrets = list(params.secrets.of_type(SECRET_TYPE))
    if len(secrets) > 1:
        raise ApiError("Multiple eia_api secrets matched; keep one applicable secret")
    key = None
    if secrets:
        scalar = secrets[0].get("api_key")
        key = scalar.as_py() if scalar is not None else None
    return validate_key(key if key is not None else os.environ.get("EIA_API_KEY"))


def route(value: str, *, root: bool = False) -> str:
    """Validate a relative metadata route without accepting URLs or traversal."""
    if root and value == "":
        return value
    if len(value) > 512 or re.fullmatch(ROUTE_PATTERN, value) is None:
        raise ApiError("route must be a relative EIA metadata path without leading/trailing slash, URL or traversal")
    if any(part.lower() in {"data", "facet", "seriesid", "v2"} for part in value.split("/")):
        raise ApiError("route must identify metadata; omit v2, data, facet and seriesid segments")
    return value


def period(value: str) -> str:
    """Accept native EIA period bounds without converting them to invented dates."""
    if (
        value
        and re.fullmatch(r"\d{4}(?:-(?:\d{2}|Q[1-4])(?:-\d{2}(?:T\d{2}(?::\d{2}(?::\d{2})?)?Z?)?)?)?", value) is None
    ):
        raise ApiError("Period bounds must use EIA's native YYYY, YYYY-MM, YYYY-Qn, date or ISO hour format")
    return value


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON object keys."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def facets_json(value: str) -> dict[str, list[str]]:
    """Parse bounded facet arrays, preserving leading zeroes in identifiers."""
    try:
        if len(value) > 20000:
            raise ValueError
        parsed = json.loads(value, object_pairs_hook=_unique)
        if not isinstance(parsed, dict) or len(parsed) > 20:
            raise ValueError
        for name, items in parsed.items():
            if re.fullmatch(FIELD_PATTERN, name) is None or not isinstance(items, list) or not 1 <= len(items) <= 100:
                raise ValueError
            if any(not isinstance(x, str) or not x or len(x) > 256 or any(ord(c) < 32 for c in x) for x in items):
                raise ValueError
            if len(set(items)) != len(items):
                raise ValueError
        return parsed
    except (ValueError, TypeError, RecursionError):
        raise ApiError(
            "facets_json must be an object of unique facet names and nonempty unique string arrays"
        ) from None


def call(endpoint: str, key: str, query: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """Return only the data envelope, never the credential-echoing request node."""
    body = request_json(endpoint, [("api_key", validate_key(key)), *(query or [])], Config.from_env())
    if "error" in body:
        raise ApiError("EIA rejected the request; check route, parameters, API key and quota")
    response = body.get("response")
    if not isinstance(response, dict):
        raise ApiError("EIA response is missing its response object")
    if "error" in response:
        raise ApiError("EIA returned an API error")
    if body.get("warning") or response.get("warning"):
        raise ApiError("EIA returned a warning; refusing potentially partial results; narrow the request")
    return response


def records(node: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Require an explicit array of object records, including empty arrays."""
    values = node.get(name)
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        raise ApiError(f"EIA response has invalid {name} records")
    return values


def count(value: Any) -> int:
    """Decode bounded nonnegative counts from integer or string envelopes."""
    if type(value) not in (str, int) or re.fullmatch(r"\d{1,12}", str(value)) is None:
        raise ApiError("EIA response has an invalid total count")
    return int(value)


def source_json(value: Any) -> str:
    """Serialize response fields without allowing non-finite JSON values."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, OverflowError):
        raise ApiError("EIA response contains invalid JSON values") from None
