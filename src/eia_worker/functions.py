"""Discover EIA API v2 metadata and stream energy observations as Arrow pages."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, ClassVar

import pyarrow as pa
from vgi.arguments import Arg
from vgi.invocation import BindResponse
from vgi.metadata import FunctionExample
from vgi.table_function import BindParams, OutputCollector, ProcessParams, TableFunctionGenerator, init_single_worker
from vgi_rpc import ArrowSerializableDataclass

from .api import (
    FIELD_PATTERN,
    SECRET_TYPE,
    SERIES_PATTERN,
    call,
    count,
    credential,
    facets_json,
    period,
    records,
    route,
    source_json,
)
from .meta import column, function_meta
from .transport import ApiError


def schema(*columns: tuple[str, pa.DataType, str]) -> pa.Schema:
    """Build fixed result columns with discovery descriptions."""
    return pa.schema([column(name, kind, description) for name, kind, description in columns])


ROUTES = schema(
    ("route", pa.string(), "Relative child route, suitable for further discovery."),
    ("id", pa.string(), "Child route identifier."),
    ("name", pa.string(), "Published route name."),
    ("description", pa.string(), "Published route description."),
)
METADATA = schema(
    ("route", pa.string(), "Requested relative metadata route."),
    ("name", pa.string(), "Published dataset name."),
    ("description", pa.string(), "Dataset scope and source notes."),
    ("start_period", pa.string(), "Earliest native period advertised by EIA."),
    ("end_period", pa.string(), "Latest native period advertised by EIA."),
    ("default_frequency", pa.string(), "Default frequency advertised by the source."),
    ("metadata_json", pa.string(), "Complete metadata response object; request echoes are excluded."),
)
FREQUENCIES = schema(
    ("frequency", pa.string(), "Frequency identifier accepted by observations()."),
    ("description", pa.string(), "Published frequency description."),
    ("query_code", pa.string(), "Legacy frequency code, when supplied."),
    ("period_format", pa.string(), "Native period format advertised by EIA."),
)
MEASURES = schema(
    ("measure", pa.string(), "Data column identifier accepted by observations()."),
    ("name", pa.string(), "Published measure alias or label."),
    ("units", pa.string(), "Source units; values are not rescaled."),
    ("metadata_json", pa.string(), "Complete source descriptor for this measure."),
)
FACETS = schema(
    ("facet", pa.string(), "Dimension identifier used as a facets_json key."),
    ("description", pa.string(), "Published dimension description."),
)
FACET_VALUES = schema(
    ("id", pa.string(), "Facet value identifier, preserving leading zeroes."),
    ("name", pa.string(), "Published facet value name."),
    ("alias", pa.string(), "Published facet value alias."),
)
OBSERVATIONS = schema(
    ("route", pa.string(), "Requested dataset route or seriesid/legacy-identifier."),
    ("period", pa.string(), "Native EIA observation period; no date is inferred."),
    ("frequency", pa.string(), "Frequency reported by the response."),
    ("measure", pa.string(), "Selected data column or the legacy series' inferred measure."),
    ("value", pa.float64(), "Numeric value in published units; NULL for documented missing/suppressed markers."),
    ("value_text", pa.string(), "Original string or canonical text of a legacy numeric value."),
    ("value_status", pa.string(), "numeric, missing, withheld or insignificant."),
    ("units", pa.string(), "Per-row source units, without rescaling."),
    ("dimensions_json", pa.string(), "All dimension IDs and labels from the original row."),
    ("source_json", pa.string(), "Complete source observation object."),
)


# Composed examples: each combines two of this worker's functions in one query. Each is
# defined once here and attached to the function that drives it; the schema-level list
# is assembled from the per-function lists in worker.py.
DEFAULT_FREQUENCY_FORMAT_EXAMPLE = FunctionExample(
    description=(
        "Pair the retail-sales dataset's default frequency with that frequency's native period "
        "format and the advertised period range, to know how to write start_period and end_period"
    ),
    sql=(
        "SELECT m.name, m.default_frequency, f.period_format, m.start_period, m.end_period "
        "FROM eia.main.metadata('electricity/retail-sales') AS m "
        "JOIN eia.main.frequencies('electricity/retail-sales') AS f ON f.frequency = m.default_frequency "
        "ORDER BY f.frequency"
    ),
)
SALES_WEIGHTED_PRICE_EXAMPLE = FunctionExample(
    description=(
        "Colorado residential 2024 electricity price weighted by monthly retail sales, beside the "
        "simple average of the 12 monthly prices (cents per kilowatt-hour)"
    ),
    sql=(
        "WITH price AS (SELECT period, value AS cents_per_kwh FROM eia.main.observations("
        "'electricity/retail-sales', 'price', 'monthly', "
        'facets_json := \'{"stateid":["CO"],"sectorid":["RES"]}\', '
        "start_period := '2024-01', end_period := '2024-12')), "
        "sales AS (SELECT period, value AS million_kwh FROM eia.main.observations("
        "'electricity/retail-sales', 'sales', 'monthly', "
        'facets_json := \'{"stateid":["CO"],"sectorid":["RES"]}\', '
        "start_period := '2024-01', end_period := '2024-12')) "
        "SELECT count(*) AS months, "
        "round(sum(price.cents_per_kwh * sales.million_kwh) / sum(sales.million_kwh), 2) AS weighted_price, "
        "round(avg(price.cents_per_kwh), 2) AS simple_average_price "
        "FROM price JOIN sales USING (period) ORDER BY months"
    ),
)
SECTOR_COVERAGE_EXAMPLE = FunctionExample(
    description=(
        "List every retail-sales sector ID and name with Colorado's 2024 annual average price, "
        "NULL for sectors with no published 2024 Colorado price"
    ),
    sql=(
        "SELECT s.id AS sectorid, s.name AS sector, o.value AS cents_per_kwh "
        "FROM eia.main.facet_values('electricity/retail-sales', 'sectorid') AS s "
        "LEFT JOIN eia.main.observations('electricity/retail-sales', 'price', 'annual', "
        "facets_json := '{\"stateid\":[\"CO\"]}', start_period := '2024', end_period := '2024') AS o "
        "ON o.dimensions_json ->> '$.sectorid' = s.id "
        "ORDER BY s.id"
    ),
)


@dataclass(frozen=True, kw_only=True)
class RootArgs:
    """Optional parent route for hierarchy discovery."""

    route: Annotated[str, Arg("route", default="", doc="Relative parent route; empty discovers the API root.")]


@dataclass(frozen=True, kw_only=True)
class RouteArgs:
    """A required relative metadata route."""

    route: Annotated[
        str, Arg(0, doc="Relative metadata route, for example electricity/retail-sales; omit v2 and data.")
    ]


@dataclass(frozen=True, kw_only=True)
class FacetArgs(RouteArgs):
    """One complete dimension-value listing."""

    facet: Annotated[str, Arg(1, pattern=FIELD_PATTERN, doc="Dimension identifier returned by facets().")]


@dataclass(frozen=True, kw_only=True)
class PageArgs:
    """Bounded source pages and native period bounds."""

    start_period: Annotated[
        str, Arg("start_period", default="", doc="Inclusive native period lower bound; empty is unbounded.")
    ]
    end_period: Annotated[
        str, Arg("end_period", default="", doc="Inclusive native period upper bound; empty is unbounded.")
    ]
    page_size: Annotated[
        int,
        Arg(
            "page_size",
            default=1000,
            ge=1,
            le=5000,
            doc="Source rows requested per page, up to EIA's 5000-row ceiling.",
        ),
    ]
    max_rows: Annotated[
        int,
        Arg(
            "max_rows", default=0, ge=0, le=10000000, doc="Explicit output cap; zero streams all matching observations."
        ),
    ]
    start_offset: Annotated[
        int, Arg("start_offset", default=0, ge=0, le=1000000000, doc="Zero-based source row offset at which to begin.")
    ]
    sort_direction: Annotated[
        str,
        Arg(
            "sort_direction",
            default="asc",
            choices=["asc", "desc"],
            doc="Period and dimension sort direction; all facet identifiers break period ties.",
        ),
    ]


@dataclass(frozen=True, kw_only=True)
class ObservationArgs(PageArgs):
    """A single selected measure with explicit frequency and facet filters."""

    route: Annotated[str, Arg(0, doc="Relative data route returned by routes(); omit v2 and data.")]
    measure: Annotated[str, Arg(1, pattern=FIELD_PATTERN, doc="One numeric data column returned by measures().")]
    frequency: Annotated[
        str, Arg(2, pattern=FIELD_PATTERN, doc="Explicit frequency identifier returned by frequencies().")
    ]
    facets_json: Annotated[
        str,
        Arg(
            "facets_json",
            default="{}",
            doc='Object of dimension names to string arrays, e.g. {"stateid":["CO"],"sectorid":["RES"]}.',
        ),
    ]


@dataclass(frozen=True, kw_only=True)
class SeriesArgs(PageArgs):
    """Legacy series translation through the documented v2 seriesid route."""

    series_id: Annotated[
        str, Arg(0, pattern=SERIES_PATTERN, doc="Legacy EIA series identifier, for example ELEC.SALES.CO-RES.A.")
    ]


@dataclass(kw_only=True)
class State(ArrowSerializableDataclass):
    """Small portable pagination state containing no credentials or source records."""

    offset: int = 0
    emitted: int = 0
    total: int = -1
    previous_digest: str = ""
    previous_identity: str = ""
    dimensions: list[str] = field(default_factory=list)
    measure: str = ""
    frequency: str = ""
    initialized: bool = False
    done: bool = False


def text(node: dict[str, Any], key: str, *, required: bool = False) -> str | None:
    """Read a string or integer identifier while preserving string leading zeroes."""
    value = node.get(key)
    if value is None and not required:
        return None
    if type(value) not in (str, int) or (required and str(value) == ""):
        raise ApiError(f"EIA response has invalid {key}")
    return str(value)


def number(value: Any) -> tuple[float | None, str | None, str]:
    """Preserve published values and known missing/suppression markers."""
    if value is None:
        return None, None, "missing"
    if type(value) not in (str, int, float):
        raise ApiError("EIA response has an invalid numeric value")
    raw = str(value)
    markers = {
        "": "missing",
        "null": "missing",
        "NA": "missing",
        "N/A": "missing",
        "--": "missing",
        "w": "withheld",
        "W": "withheld",
        "*": "insignificant",
    }
    if raw in markers:
        return None, raw, markers[raw]
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", raw) is None:
        raise ApiError("EIA response contains an unrecognized numeric value")
    numeric = float(raw)
    if not math.isfinite(numeric):
        raise ApiError("EIA response contains a non-finite numeric value")
    return numeric, raw, "numeric"


def data_page(args: PageArgs, state: State, key: str) -> list[dict[str, Any]]:
    """Request and validate one stable, bounded observation page."""
    query: list[tuple[str, str]] = []
    if isinstance(args, ObservationArgs):
        endpoint = args.route + "/data/"
        if not state.initialized:
            metadata = call(args.route + "/", key)
            measures = metadata.get("data")
            if not isinstance(measures, dict) or args.measure not in measures:
                raise ApiError("measure is not advertised by the selected EIA route")
            frequencies = {text(row, "id", required=True) for row in records(metadata, "frequency")}
            if args.frequency not in frequencies:
                raise ApiError("frequency is not advertised by the selected EIA route")
            state.dimensions = [str(text(row, "id", required=True)) for row in records(metadata, "facets")]
            if len(set(state.dimensions)) != len(state.dimensions) or any(
                re.fullmatch(FIELD_PATTERN, x) is None for x in state.dimensions
            ):
                raise ApiError("EIA metadata has invalid or duplicate facets")
            if set(facets_json(args.facets_json)) - set(state.dimensions):
                raise ApiError("facets_json includes dimensions not advertised by the EIA route")
            state.measure = args.measure
            state.frequency = args.frequency
        query.extend([("data[0]", args.measure), ("frequency", args.frequency)])
        for name, values in facets_json(args.facets_json).items():
            query.extend((f"facets[{name}][]", value) for value in values)
        selected_route = args.route
    elif isinstance(args, SeriesArgs):
        endpoint = "seriesid/" + args.series_id
        selected_route = endpoint
    else:
        raise NotImplementedError
    if args.start_period:
        query.append(("start", args.start_period))
    if args.end_period:
        query.append(("end", args.end_period))
    for i, name in enumerate(["period", *state.dimensions]):
        query.extend([(f"sort[{i}][column]", name), (f"sort[{i}][direction]", args.sort_direction)])
    length = min(args.page_size, args.max_rows - state.emitted) if args.max_rows else args.page_size
    query.extend([("offset", str(state.offset)), ("length", str(length))])
    response = call(endpoint, key, query)
    raw = records(response, "data")
    total = count(response.get("total"))
    if state.total != -1 and state.total != total:
        raise ApiError("EIA result count changed during pagination; retry with a fixed historical period window")
    if len(raw) != min(length, max(0, total - state.offset)):
        raise ApiError("EIA returned a short, empty or oversized page inconsistent with its total")
    frequency = text(response, "frequency", required=True)
    if state.frequency and frequency != state.frequency:
        raise ApiError("EIA response frequency changed or differs from the requested frequency")
    state.frequency = str(frequency)
    digest = hashlib.sha256(source_json(raw).encode()).hexdigest()
    if raw and digest == state.previous_digest:
        raise ApiError("EIA repeated a page; refusing duplicate observations")
    result = []
    identities: set[str] = set()
    for row in raw:
        if not state.measure:
            candidates = [
                name.removesuffix("-units")
                for name in row
                if name.endswith("-units") and name.removesuffix("-units") in row
            ]
            if len(candidates) != 1:
                raise ApiError(
                    "Legacy series has ambiguous measure metadata; use observations() with an explicit measure"
                )
            state.measure = candidates[0]
        measure = state.measure
        if measure not in row or any(name not in row for name in state.dimensions):
            raise ApiError("EIA observation is missing its requested measure or dimension")
        native_period = text(row, "period", required=True)
        dimensions = {name: value for name, value in row.items() if name not in {"period", measure, measure + "-units"}}
        identity = source_json([native_period, *[row[name] for name in state.dimensions]])
        if identity in identities or identity == state.previous_identity:
            raise ApiError("EIA returned duplicate observation identities")
        identities.add(identity)
        numeric, raw_value, status = number(row[measure])
        result.append(
            {
                "route": selected_route,
                "period": native_period,
                "frequency": frequency,
                "measure": measure,
                "value": numeric,
                "value_text": raw_value,
                "value_status": status,
                "units": text(row, measure + "-units"),
                "dimensions_json": source_json(dimensions),
                "source_json": source_json(row),
            }
        )
        state.previous_identity = identity
    state.initialized = True
    state.previous_digest = digest
    state.total = total
    state.offset += len(raw)
    state.emitted += len(raw)
    state.done = state.offset >= total or bool(args.max_rows and state.emitted >= args.max_rows)
    return result


@init_single_worker
class EiaFunction[T: RootArgs | RouteArgs | PageArgs](TableFunctionGenerator[T, State]):
    """Shared authenticated table function with portable streaming state."""

    FIXED_SCHEMA: ClassVar[pa.Schema]

    @classmethod
    def on_bind(cls, params: BindParams[T]) -> BindResponse:
        """Validate query structure and declare the upstream secret dependency."""
        args = params.args
        if isinstance(args, (RootArgs, RouteArgs, ObservationArgs)):
            route(args.route, root=isinstance(args, RootArgs))
        if isinstance(args, PageArgs):
            period(args.start_period)
            period(args.end_period)
            if args.start_period and args.end_period and args.start_period > args.end_period:
                raise ApiError("start_period must not be after end_period")
        if isinstance(args, ObservationArgs):
            facets_json(args.facets_json)
        params.secrets.get(SECRET_TYPE)
        return BindResponse(output_schema=cls.FIXED_SCHEMA)

    @classmethod
    def initial_state(cls, params: ProcessParams[T]) -> State:
        """Begin with a small credential-free source cursor."""
        return State(offset=params.args.start_offset if isinstance(params.args, PageArgs) else 0)

    @classmethod
    def fetch(cls, args: T, state: State, key: str) -> list[dict[str, Any]]:
        """Fetch one service-specific result or observation page."""
        if isinstance(args, PageArgs):
            return data_page(args, state, key)
        raise NotImplementedError

    @classmethod
    def process(cls, params: ProcessParams[T], state: State, out: OutputCollector) -> None:
        """Emit an Arrow batch after complete validation of its source page."""
        if state.done:
            out.finish()
            return
        rows = cls.fetch(params.args, state, credential(params))
        try:
            batch = pa.RecordBatch.from_pylist(rows, schema=params.output_schema)
        except (pa.ArrowException, ValueError, OverflowError):
            raise ApiError("EIA response cannot be represented by the declared schema") from None
        if not isinstance(params.args, PageArgs):
            state.done = True
        out.emit(batch)


class Routes(EiaFunction[RootArgs]):
    """Discover direct child routes."""

    FIXED_SCHEMA = ROUTES
    Meta = function_meta(
        "routes",
        "Discover EIA routes",
        (
            "List direct children of a parent route, or the API root when route is empty. Leaf "
            "datasets return no children; inspect metadata() and measures() to query them."
        ),
        ROUTES,
        "SELECT route, name FROM eia.main.routes(route := 'electricity') ORDER BY route",
    )

    @classmethod
    def fetch(cls, args: RootArgs, state: State, key: str) -> list[dict[str, Any]]:
        """Read child identifiers without exposing request echoes."""
        node = call(args.route + "/" if args.route else "", key)
        if "routes" not in node and isinstance(node.get("data"), dict):
            return []
        result = []
        for row in records(node, "routes"):
            identifier = str(text(row, "id", required=True))
            child = (args.route + "/" if args.route else "") + identifier
            route(child)
            result.append(
                {"route": child, "id": identifier, "name": text(row, "name"), "description": text(row, "description")}
            )
        return result


class Metadata(EiaFunction[RouteArgs]):
    """Inspect complete dataset descriptors."""

    FIXED_SCHEMA = METADATA
    Meta = function_meta(
        "metadata",
        "Inspect EIA dataset metadata",
        (
            "Return one metadata row containing source notes, period bounds and the complete "
            "response descriptor. The API request echo and its credential are excluded."
        ),
        METADATA,
        "SELECT name, start_period, end_period FROM eia.main.metadata('electricity/retail-sales')",
        extra_examples=(DEFAULT_FREQUENCY_FORMAT_EXAMPLE,),
    )

    @classmethod
    def fetch(cls, args: RouteArgs, state: State, key: str) -> list[dict[str, Any]]:
        """Read the source metadata descriptor."""
        node = call(args.route + "/", key)
        return [
            {
                "route": args.route,
                "name": text(node, "name"),
                "description": text(node, "description"),
                "start_period": text(node, "startPeriod"),
                "end_period": text(node, "endPeriod"),
                "default_frequency": text(node, "defaultFrequency"),
                "metadata_json": source_json(node),
            }
        ]


class Frequencies(EiaFunction[RouteArgs]):
    """Discover valid frequency identifiers and native period formats."""

    FIXED_SCHEMA = FREQUENCIES
    Meta = function_meta(
        "frequencies",
        "Discover EIA frequencies",
        (
            "List the selected leaf dataset's supported frequency identifiers and native period "
            "formats. Use the frequency identifier as observations()'s third argument."
        ),
        FREQUENCIES,
        ("SELECT frequency, period_format FROM eia.main.frequencies('electricity/retail-sales') ORDER BY frequency"),
    )

    @classmethod
    def fetch(cls, args: RouteArgs, state: State, key: str) -> list[dict[str, Any]]:
        """Read supported frequency descriptors."""
        return [
            {
                "frequency": text(row, "id", required=True),
                "description": text(row, "description"),
                "query_code": text(row, "query"),
                "period_format": text(row, "format"),
            }
            for row in records(call(args.route + "/", key), "frequency")
        ]


class Measures(EiaFunction[RouteArgs]):
    """Discover selected datasets' numeric data columns."""

    FIXED_SCHEMA = MEASURES
    Meta = function_meta(
        "measures",
        "Discover EIA measures",
        (
            "List numeric data columns and published units for a leaf dataset. Select one measure "
            "per observations() call to keep source row counts and output row counts identical."
        ),
        MEASURES,
        "SELECT measure, units FROM eia.main.measures('electricity/retail-sales') ORDER BY measure",
    )

    @classmethod
    def fetch(cls, args: RouteArgs, state: State, key: str) -> list[dict[str, Any]]:
        """Read data-column descriptors."""
        data = call(args.route + "/", key).get("data")
        if not isinstance(data, dict) or any(not isinstance(value, dict) for value in data.values()):
            raise ApiError("EIA metadata has invalid measure descriptors; choose a leaf dataset")
        return [
            {
                "measure": name,
                "name": text(value, "alias"),
                "units": text(value, "units"),
                "metadata_json": source_json(value),
            }
            for name, value in data.items()
        ]


class Facets(EiaFunction[RouteArgs]):
    """Discover filterable dimensions."""

    FIXED_SCHEMA = FACETS
    Meta = function_meta(
        "facets",
        "Discover EIA dimensions",
        (
            "List dimension identifiers accepted as facets_json keys by observations(). Use "
            "facet_values() to discover their available string identifiers."
        ),
        FACETS,
        "SELECT facet, description FROM eia.main.facets('electricity/retail-sales') ORDER BY facet",
    )

    @classmethod
    def fetch(cls, args: RouteArgs, state: State, key: str) -> list[dict[str, Any]]:
        """Read filterable dimension descriptors."""
        return [
            {"facet": text(row, "id", required=True), "description": text(row, "description")}
            for row in records(call(args.route + "/", key), "facets")
        ]


class FacetValues(EiaFunction[FacetArgs]):
    """Read a complete bounded facet-value listing."""

    FIXED_SCHEMA = FACET_VALUES
    Meta = function_meta(
        "facet_values",
        "Discover EIA dimension values",
        (
            "List a dimension's value IDs, names and aliases. This endpoint has no documented "
            "pagination; the worker validates totalFacets and fails if the bounded response is "
            "incomplete."
        ),
        FACET_VALUES,
        "SELECT id, name FROM eia.main.facet_values('electricity/retail-sales', 'sectorid') ORDER BY id",
        extra_examples=(SECTOR_COVERAGE_EXAMPLE,),
    )

    @classmethod
    def fetch(cls, args: FacetArgs, state: State, key: str) -> list[dict[str, Any]]:
        """Require all advertised facet values to be present exactly once."""
        node = call(args.route + "/facet/" + args.facet + "/", key)
        rows = records(node, "facets")
        if len(rows) != count(node.get("totalFacets")):
            raise ApiError("EIA facet listing is incomplete; refusing silent truncation")
        result = [
            {"id": text(row, "id", required=True), "name": text(row, "name"), "alias": text(row, "alias")}
            for row in rows
        ]
        if len({row["id"] for row in result}) != len(result):
            raise ApiError("EIA facet listing contains duplicate identifiers")
        return result


class Observations(EiaFunction[ObservationArgs]):
    """Stream one measure with explicit frequency and source filters."""

    FIXED_SCHEMA = OBSERVATIONS
    Meta = function_meta(
        "observations",
        "Read EIA observations",
        (
            "Stream one measure at an explicit frequency. Required positional arguments are "
            "route, measure and frequency. All other arguments are named; their display order has "
            "no binding significance. Metadata validates filters and supplies "
            "dimension tie-breakers for stable sorting. Periods, units, missing markers and "
            "dimensions are preserved; source total changes or incomplete pages fail the query."
        ),
        OBSERVATIONS,
        (
            "SELECT period, value, units FROM eia.main.observations('electricity/retail-sales', "
            '\'price\', \'monthly\', facets_json := \'{"stateid":["CO"],"sectorid":["RES"]}\', '
            "start_period := '2024-01', end_period := '2024-03') ORDER BY period"
        ),
        extra_examples=(SALES_WEIGHTED_PRICE_EXAMPLE,),
    )


class Series(EiaFunction[SeriesArgs]):
    """Read legacy identifiers through EIA's v2 translation endpoint."""

    FIXED_SCHEMA = OBSERVATIONS
    Meta = function_meta(
        "series",
        "Read a legacy EIA series",
        (
            "Translate one legacy series ID through the v2 seriesid endpoint and stream its "
            "observations. Frequency and the single measure are inferred from the response. "
            "Ambiguous measure metadata fails; use observations() for explicit selection."
        ),
        OBSERVATIONS,
        (
            "SELECT period, value, units FROM eia.main.series('ELEC.SALES.CO-RES.A', start_period "
            ":= '2022', end_period := '2024') ORDER BY period"
        ),
    )


FUNCTIONS: list[type[Any]] = [Routes, Metadata, Frequencies, Measures, Facets, FacetValues, Observations, Series]
