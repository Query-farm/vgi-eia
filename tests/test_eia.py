"""EIA contract tests exercise real worker processes against a local API."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from vgi.arguments import Arguments
from vgi.client import Client
from vgi.invocation import BindResponse

from eia_worker.api import facets_json, route, validate_key
from eia_worker.auth import validate_http_auth
from eia_worker.functions import State, number
from eia_worker.transport import ApiError
from test.mock_api.api_server import API_KEY, ApiServer


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[ApiServer]:
    server = ApiServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("EIA_API_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("EIA_ALLOW_INSECURE_HTTP", "1")
    monkeypatch.setenv("EIA_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("EIA_API_KEY", API_KEY)
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def eia_client(api: ApiServer) -> Iterator[Client]:
    with Client([sys.executable, "-m", "eia_worker.worker"], pool=None) as client:
        yield client


def table(client: Client, name: str, *positional: Any, secret_key: str | None = None, **named: Any) -> pa.Table:
    bound: list[BindResponse] = []
    batches = list(
        client.table_function(
            function_name=name,
            schema_path=["main"],
            secrets={"fixture": {"type": "eia_api", "api_key": secret_key}},
            bind_result_callback=bound.append,
            arguments=Arguments(
                positional=tuple(pa.scalar(v) for v in positional), named={k: pa.scalar(v) for k, v in named.items()}
            ),
        )
    )
    return pa.Table.from_batches(batches, schema=bound[-1].output_schema)


ROUTE = "electricity/retail-sales"


def test_discovery_and_safe_metadata(eia_client: Client) -> None:
    assert table(eia_client, "routes")["route"].to_pylist() == ["electricity"]
    assert table(eia_client, "routes", route="electricity")["route"].to_pylist() == [ROUTE]
    assert table(eia_client, "routes", route=ROUTE).num_rows == 0
    metadata = table(eia_client, "metadata", ROUTE)
    assert API_KEY not in str(metadata.to_pylist())
    assert json.loads(metadata["metadata_json"][0].as_py())["startPeriod"] == "2024-01"
    assert table(eia_client, "frequencies", ROUTE)["frequency"].to_pylist() == ["monthly", "annual"]
    assert table(eia_client, "measures", ROUTE)["measure"].to_pylist() == ["price", "sales"]
    assert table(eia_client, "facets", ROUTE)["facet"].to_pylist() == ["stateid", "sectorid"]
    assert table(eia_client, "facet_values", ROUTE, "stateid")["id"].to_pylist() == ["CO", "01"]


def test_pages_preserve_markers_units_and_dimensions(eia_client: Client, api: ApiServer) -> None:
    result = table(eia_client, "observations", ROUTE, "price", "monthly", page_size=2)
    assert result["value"].to_pylist() == [10.25, None, None, None, 12.75]
    assert result["value_text"].to_pylist() == ["10.25", "null", "w", "*", "12.75"]
    assert result["value_status"].to_pylist() == ["numeric", "missing", "withheld", "insignificant", "numeric"]
    assert result["units"][0].as_py() == "cents per kilowatthour"
    assert json.loads(result["dimensions_json"][0].as_py())["stateid"] == "CO"
    pages = [r["query"] for r in api.requests if r["endpoint"].endswith("data")]
    assert [p["offset"] for p in pages] == [["0"], ["2"], ["4"]]
    assert pages[0]["sort[1][column]"] == ["stateid"]
    assert pages[0]["sort[2][column]"] == ["sectorid"]
    assert API_KEY not in str(result.to_pylist())


def test_caps_offset_bounds_filters_and_empty(eia_client: Client, api: ApiServer) -> None:
    result = table(eia_client, "observations", ROUTE, "price", "monthly", page_size=2, max_rows=3)
    assert result["period"].to_pylist() == ["2024-01", "2024-02", "2024-03"]
    assert api.requests[-1]["query"]["length"] == ["1"]
    assert api.requests[-1]["query"]["offset"] == ["2"]
    result = table(
        eia_client,
        "observations",
        ROUTE,
        "price",
        "monthly",
        start_offset=1,
        start_period="2024-02",
        end_period="2024-04",
        sort_direction="desc",
        page_size=1,
    )
    assert result["period"].to_pylist() == ["2024-03", "2024-02"]
    assert table(eia_client, "observations", ROUTE, "price", "monthly", facets_json='{"stateid":["01"]}').num_rows == 0
    assert table(eia_client, "observations", ROUTE, "price", "monthly", start_offset=100).num_rows == 0


def test_legacy_numeric_values_and_periods(eia_client: Client) -> None:
    result = table(eia_client, "series", "ELEC.SALES.CO-RES.A", start_period="2022", end_period="2024", page_size=1)
    assert result["period"].to_pylist() == ["2022", "2023", "2024"]
    assert result["value"].to_pylist() == [12.75, 13.0, 14.5]
    assert result["measure"].to_pylist() == ["sales"] * 3
    assert result["frequency"].to_pylist() == ["annual"] * 3


@pytest.mark.parametrize(
    "mode,match",
    [
        ("STALLED", "short, empty"),
        ("CHANGED", "count changed"),
        ("REPEAT", "repeated"),
        ("BAD_VALUE", "unrecognized numeric"),
        ("MISSING_VALUE", "missing its requested"),
        ("DUPLICATE", "duplicate observation"),
        ("FREQUENCY", "frequency changed"),
        ("WARNING", "warning"),
        ("ERROR", "rejected the request"),
    ],
)
def test_corrupt_pages_fail_safely(eia_client: Client, api: ApiServer, mode: str, match: str) -> None:
    api.mode = mode
    with pytest.raises(Exception, match=match) as exc:
        table(eia_client, "observations", ROUTE, "price", "monthly", page_size=2)
    assert API_KEY not in str(exc.value)


@pytest.mark.parametrize("mode", ["PARTIAL_FACETS", "DUPLICATE_FACETS"])
def test_incomplete_or_duplicate_facet_list_fails(eia_client: Client, api: ApiServer, mode: str) -> None:
    api.mode = mode
    with pytest.raises(Exception, match=r"incomplete|duplicate"):
        table(eia_client, "facet_values", ROUTE, "stateid")


def test_ambiguous_legacy_measure_fails(eia_client: Client, api: ApiServer) -> None:
    api.mode = "AMBIGUOUS"
    with pytest.raises(Exception, match="ambiguous measure"):
        table(eia_client, "series", "ELEC.SALES.CO-RES.A")


def test_retries_and_redirects(eia_client: Client, api: ApiServer) -> None:
    api.mode = "RETRY"
    assert table(eia_client, "routes").num_rows == 1
    assert api.attempts == 2
    api.mode = "REDIRECT"
    with pytest.raises(Exception, match="HTTP 302") as exc:
        table(eia_client, "routes")
    assert API_KEY not in str(exc.value)


def test_secrets_override_environment_and_errors_redact(api: ApiServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EIA_API_KEY", "incorrect-environment-key")
    with Client([sys.executable, "-m", "eia_worker.worker"], pool=None) as client:
        assert table(client, "routes", secret_key=API_KEY).num_rows == 1
        with pytest.raises(Exception, match="HTTP 403") as exc:
            table(client, "routes", secret_key="wrong-secret-key")
        assert "wrong-secret-key" not in str(exc.value)
    monkeypatch.delenv("EIA_API_KEY")
    with (
        Client([sys.executable, "-m", "eia_worker.worker"], pool=None) as client,
        pytest.raises(Exception, match="EIA_API_KEY"),
    ):
        table(client, "routes")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://example.org",
        "../private",
        "/electricity",
        "electricity/data",
        "electricity?api_key=bad",
        "v2/electricity",
        "electricity/%2e%2e",
    ],
)
def test_invalid_routes(value: str) -> None:
    with pytest.raises(ApiError):
        route(value)


@pytest.mark.parametrize(
    "value", ["[]", '{"x":[]}', '{"x":[1]}', '{"x":["a","a"]}', '{"x":["a"],"x":["b"]}', '{"bad[key]":["a"]}']
)
def test_invalid_facets(value: str) -> None:
    with pytest.raises(ApiError):
        facets_json(value)


@pytest.mark.parametrize("value", [None, "", "bad key", "bad&api_key=x"])
def test_invalid_keys_are_not_echoed(value: str | None) -> None:
    with pytest.raises(ApiError) as exc:
        validate_key(value)
    if value:
        assert value not in str(exc.value)


@pytest.mark.parametrize("value", [True, {}, "NaN", "Infinity", "1,000", "corrupt", 1e300 * 1e300])
def test_unrecognized_numbers_fail(value: Any) -> None:
    with pytest.raises(ApiError):
        number(value)


def test_metadata_validation_and_bad_bounds(eia_client: Client, api: ApiServer) -> None:
    with pytest.raises(Exception, match="start_period must"):
        table(eia_client, "observations", ROUTE, "price", "monthly", start_period="2025", end_period="2024")
    assert not api.requests
    with pytest.raises(Exception, match="measure is not"):
        table(eia_client, "observations", ROUTE, "invalid", "monthly")
    with pytest.raises(Exception, match="frequency is not"):
        table(eia_client, "observations", ROUTE, "price", "hourly")
    with pytest.raises(Exception, match="dimensions not advertised"):
        table(eia_client, "observations", ROUTE, "price", "monthly", facets_json='{"unknown":["x"]}')


def test_state_roundtrip() -> None:
    state = State(
        offset=2, total=5, emitted=2, measure="price", frequency="monthly", dimensions=["stateid"], initialized=True
    )
    encoded = state.serialize_to_bytes()
    assert API_KEY.encode() not in encoded
    assert State.deserialize_from_bytes(encoded) == state


WORKER_TOKEN = "fixture-only-worker-token-with-32-characters"


@pytest.fixture()
def eia_http(api: ApiServer, tmp_path: Path) -> Iterator[str]:
    port_file = tmp_path / "port"
    environment = os.environ.copy()
    environment.update(VGI_BEARER_TOKENS=f"{WORKER_TOKEN}=analyst", VGI_PROXY_PROOF_MODE="off")
    environment.pop("VGI_JWT_ISSUER", None)
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "eia_worker.worker", "--http", "--port", "0", "--port-file", str(port_file)],
            stdout=log,
            stderr=log,
            env=environment,
        )
        try:
            deadline = time.monotonic() + 20
            while not port_file.exists():
                assert process.poll() is None, "HTTP worker exited before publishing its port"
                if time.monotonic() >= deadline:
                    pytest.fail("HTTP worker did not start within 20 seconds")
                time.sleep(0.05)
            location = "http://127.0.0.1:" + port_file.read_text().strip()
            while True:
                try:
                    with urllib.request.urlopen(location + "/health", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    if process.poll() is not None or time.monotonic() >= deadline:
                        pytest.fail("HTTP worker did not become ready")
                    time.sleep(0.05)
            yield location
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.seek(0)
            messages = log.read()
            assert WORKER_TOKEN not in messages
            assert API_KEY not in messages


@pytest.mark.parametrize("token", [None, "incorrect-token", API_KEY])
def test_http_rpc_rejects_missing_or_wrong_bearer(eia_http: str, api: ApiServer, token: str | None) -> None:
    headers = {"Authorization": "Bearer " + token} if token else {}
    request = urllib.request.Request(eia_http + "/__describe__", headers=headers)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 401
    exc.value.close()
    with (
        pytest.raises(Exception, match="authentication rejected"),
        Client(transport="http", base_url=eia_http, bearer_token=token, pool=None) as client,
    ):
        table(client, "routes")
    assert api.requests == []


def test_http_bearer_authenticated_pages_and_secrets(eia_http: str, api: ApiServer) -> None:
    with Client(transport="http", base_url=eia_http, bearer_token=WORKER_TOKEN, pool=None) as client:
        result = table(
            client, "observations", "electricity/retail-sales", "price", "monthly", page_size=1, secret_key=API_KEY
        )
    assert result.num_rows == 5
    assert len(api.requests) == 6
    assert all(query["query"]["api_key"] == [API_KEY] for query in api.requests)
    assert all(WORKER_TOKEN not in str(query) for query in api.requests)


def test_http_health_remains_available_without_bearer(eia_http: str) -> None:
    with urllib.request.urlopen(eia_http + "/health", timeout=5) as response:
        assert response.status == 200


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        ",",
        WORKER_TOKEN,
        "=analyst",
        f"{WORKER_TOKEN}=",
        "short=analyst",
        f"{WORKER_TOKEN}=analyst,",
        f"{WORKER_TOKEN}=analyst,{WORKER_TOKEN}=other",
    ],
)
def test_bad_bearer_configuration_fails_without_exposing_token(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("VGI_BEARER_TOKENS", value)
    with pytest.raises(ValueError, match="Invalid VGI_BEARER_TOKENS") as exc:
        validate_http_auth()
    assert WORKER_TOKEN not in str(exc.value)


def test_bearer_configuration_accepts_multiple_principals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VGI_BEARER_TOKENS", f"{WORKER_TOKEN}=analyst,{WORKER_TOKEN}-second=service")
    validate_http_auth()
    monkeypatch.delenv("VGI_BEARER_TOKENS")
    validate_http_auth()


def test_bad_bearer_configuration_stops_http_startup(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["VGI_BEARER_TOKENS"] = WORKER_TOKEN
    port_file = tmp_path / "port"
    result = subprocess.run(
        [sys.executable, "-m", "eia_worker.worker", "--http", "--port-file", str(port_file)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Invalid VGI_BEARER_TOKENS" in result.stderr
    assert WORKER_TOKEN not in result.stderr + result.stdout
    assert not port_file.exists()
