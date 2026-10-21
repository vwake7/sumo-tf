"""Minimal read-only Sumo Logic explorer."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.catalog import KINDS, get_object, list_kind, nav_payload
from app.stackql_client import client, region

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

app = FastAPI(title="Sumo explorer", docs_url=None, redoc_url=None)
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/nav")
def nav() -> dict:
    try:
        return nav_payload()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/kinds/{kind_id}")
def kind_list(kind_id: str) -> dict:
    if kind_id not in KINDS:
        raise HTTPException(status_code=404, detail="Unknown resource kind")
    try:
        return list_kind(kind_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/kinds/{kind_id}/objects/{object_id}")
def kind_object(kind_id: str, object_id: str) -> dict:
    if kind_id not in KINDS:
        raise HTTPException(status_code=404, detail="Unknown resource kind")
    try:
        return get_object(kind_id, object_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/refresh")
def refresh() -> dict:
    client.clear()
    return {"ok": True, "region": region()}
