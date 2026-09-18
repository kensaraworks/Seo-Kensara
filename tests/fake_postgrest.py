"""A minimal in-process stand-in for Supabase's PostgREST API.

Implements just enough of the protocol for the store's round-trips: `select`,
`order`, `limit`, `eq.` filters, and `Prefer: resolution=merge-duplicates`
upserts keyed by `on_conflict`. Used by the Supabase integration tests so they
exercise the real httpx calls without needing a Supabase project.
"""
from __future__ import annotations

import threading
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

SERVICE_KEY = "test-service-key"


def build_app(tables: dict[str, list[dict[str, Any]]]) -> FastAPI:
    app = FastAPI()

    def authorized(request: Request) -> bool:
        return (
            request.headers.get("apikey") == SERVICE_KEY
            and request.headers.get("authorization") == f"Bearer {SERVICE_KEY}"
        )

    @app.get("/rest/v1/{table}")
    async def select(table: str, request: Request):
        if not authorized(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        rows = list(tables.get(table, []))

        for key, value in request.query_params.items():
            if key in ("select", "order", "limit", "offset", "on_conflict"):
                continue
            if value.startswith("eq."):
                wanted = value[3:]
                rows = [r for r in rows if str(r.get(key)) == wanted]

        order = request.query_params.get("order")
        if order:
            column, _, direction = order.partition(".")
            rows.sort(key=lambda r: (r.get(column) is None, r.get(column) or ""), reverse=direction == "desc")

        offset = int(request.query_params.get("offset", 0))
        rows = rows[offset:]
        if "limit" in request.query_params:
            rows = rows[: int(request.query_params["limit"])]
        return JSONResponse(rows)

    @app.post("/rest/v1/{table}")
    async def upsert(table: str, request: Request):
        if not authorized(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        payload = await request.json()
        records = payload if isinstance(payload, list) else [payload]
        store = tables.setdefault(table, [])

        merge = "merge-duplicates" in request.headers.get("prefer", "")
        key = request.query_params.get("on_conflict")

        written = []
        for record in records:
            existing = None
            if merge and key:
                existing = next((r for r in store if r.get(key) == record.get(key)), None)
            if existing is not None:
                existing.update(record)
                written.append(existing)
            else:
                store.append(dict(record))
                written.append(record)
        return JSONResponse(written, status_code=201)

    return app


class FakeSupabase:
    """Runs the fake PostgREST on a background thread for the duration of a test."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None, port: int = 8899):
        self.tables = tables if tables is not None else {}
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "FakeSupabase":
        config = uvicorn.Config(
            build_app(self.tables), host="127.0.0.1", port=self.port, log_level="error"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = threading.Event()
        while not self._server.started:
            if deadline.wait(0.05):
                break
        return self

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
