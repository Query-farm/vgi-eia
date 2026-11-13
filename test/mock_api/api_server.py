"""Deterministic EIA metadata, observations, legacy series and error fixtures."""

from __future__ import annotations

import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

API_KEY = "fixture-eia-key-with-40-characters-00000000"
METADATA: dict[str, Any] = {
    "id": "retail-sales",
    "name": "Electricity Retail Sales",
    "description": "Synthetic test dataset",
    "startPeriod": "2024-01",
    "endPeriod": "2024-05",
    "defaultFrequency": "monthly",
    "frequency": [
        {"id": "monthly", "description": "Monthly", "query": "M", "format": "YYYY-MM"},
        {"id": "annual", "description": "Annual", "query": "A", "format": "YYYY"},
    ],
    "facets": [{"id": "stateid", "description": "State"}, {"id": "sectorid", "description": "Sector"}],
    "data": {
        "price": {"alias": "Electricity price", "units": "cents per kilowatthour"},
        "sales": {"alias": "Electricity sales", "units": "million kilowatt hours"},
    },
}


class ApiServer(ThreadingHTTPServer):
    """Offline API with inspectable requests and deliberate failure modes."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests: list[dict[str, Any]] = []
        self.mode = ""
        self.attempts = 0


class Handler(BaseHTTPRequestHandler):
    """Serve source-shaped envelopes, including the credential-echoing request node."""

    server: ApiServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def send_json(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        endpoint = url.path.strip("/")
        query = parse_qs(url.query)
        self.server.requests.append({"endpoint": endpoint, "query": query})
        self.server.attempts += 1
        mode = self.server.mode
        if query.get("api_key") != [API_KEY]:
            self.send_json(403, {"error": "Invalid credential " + str(query.get("api_key"))})
            return
        if mode == "RETRY" and self.server.attempts == 1:
            self.send_json(429, {}, {"Retry-After": "0"})
            return
        if mode == "REDIRECT":
            self.send_json(302, {}, {"Location": "https://example.invalid/"})
            return
        if mode == "ERROR":
            self.send_json(200, {"error": "bad " + API_KEY})
            return
        response: dict[str, Any]
        if endpoint in {"", "electricity"}:
            child = "electricity" if not endpoint else "retail-sales"
            response = {"routes": [{"id": child, "name": child, "description": "Fixture route"}]}
        elif endpoint == "electricity/retail-sales":
            response = copy.deepcopy(METADATA)
        elif endpoint.startswith("electricity/retail-sales/facet/"):
            facet = endpoint.split("/")[-1]
            facet_ids = ["CO", "01"] if facet == "stateid" else ["RES", "COM"]
            response = {"totalFacets": "2", "facets": [{"id": v, "name": v, "alias": "(" + v + ")"} for v in facet_ids]}
            if mode == "PARTIAL_FACETS":
                response["facets"].pop()
            if mode == "DUPLICATE_FACETS":
                response["facets"][1] = response["facets"][0]
        elif endpoint == "electricity/retail-sales/data" or endpoint.startswith("seriesid/"):
            legacy = endpoint.startswith("seriesid/")
            measure = "sales" if legacy else query.get("data[0]", ["price"])[0]
            frequency = "annual" if legacy else query.get("frequency", ["monthly"])[0]
            values: list[Any] = [10.25, 11.5, 12.75, 13.0, 14.5] if legacy else ["10.25", "null", "w", "*", "12.75"]
            rows = [
                {
                    "period": 2020 + i if legacy else f"2024-0{i + 1}",
                    "stateid": "CO",
                    "stateDescription": "Colorado",
                    "sectorid": "RES",
                    "sectorName": "residential",
                    measure: value,
                    measure + "-units": METADATA["data"][measure]["units"],
                }
                for i, value in enumerate(values)
            ]
            if not legacy:
                for facet in ["stateid", "sectorid"]:
                    choices = query.get(f"facets[{facet}][]")
                    if choices:
                        rows = [row for row in rows if row[facet] in choices]
            if query.get("start"):
                rows = [row for row in rows if str(row["period"]) >= query["start"][0]]
            if query.get("end"):
                rows = [row for row in rows if str(row["period"]) <= query["end"][0]]
            rows.sort(key=lambda row: str(row["period"]), reverse=query.get("sort[0][direction]") == ["desc"])
            offset, length = int(query.get("offset", ["0"])[0]), int(query.get("length", ["1000"])[0])
            total = len(rows)
            selected = rows[offset : offset + length]
            if mode == "STALLED":
                selected = []
            if mode == "CHANGED" and offset:
                total += 1
            if mode == "REPEAT" and offset:
                selected = rows[:length]
            if mode == "BAD_VALUE" and selected:
                selected[0][measure] = "not-a-number"
            if mode == "MISSING_VALUE" and selected:
                selected[0].pop(measure)
            if mode == "DUPLICATE" and len(selected) > 1:
                selected[1] = selected[0]
            if mode == "FREQUENCY":
                frequency = "incorrect"
            if mode == "AMBIGUOUS" and selected:
                selected[0].update(other="1", **{"other-units": "watts"})
            response = {
                "total": str(total),
                "frequency": frequency,
                "dateFormat": "YYYY" if legacy else "YYYY-MM",
                "data": selected,
            }
        else:
            self.send_json(404, {"error": "Unknown route"})
            return
        envelope = {"response": response, "request": {"params": {"api_key": API_KEY}}, "apiVersion": "2.1.14"}
        if mode == "WARNING":
            envelope["warning"] = "sensitive " + API_KEY
        self.send_json(200, envelope)


if __name__ == "__main__":
    server = ApiServer()
    print(f"PORT:{server.server_port}", flush=True)
    server.serve_forever()
