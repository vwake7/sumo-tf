"""Minimal read-only Sumo Logic explorer."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.catalog import (
    all_kinds,
    clear_schema,
    get_object,
    list_kind,
    nav_payload,
    preload,
)
from app.tfgen import generate as generate_tf
from app.stackql_client import client, region

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    preload()
    yield


app = FastAPI(title="Sumo explorer", docs_url=None, redoc_url=None, lifespan=lifespan)
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class GenerateBody(BaseModel):
    kinds: list[str] = Field(default_factory=list)
    objects: list[dict[str, Any]] = Field(default_factory=list)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/nav")
def nav() -> dict:
    try:
        return nav_payload()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/kinds/{kind_id}")
def kind_list(kind_id: str) -> dict:
    if kind_id not in all_kinds():
        raise HTTPException(status_code=404, detail="Unknown resource")
    try:
        return list_kind(kind_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/kinds/{kind_id}/object")
def kind_object(kind_id: str, id: str) -> dict:
    if kind_id not in all_kinds():
        raise HTTPException(status_code=404, detail="Unknown resource")
    try:
        return get_object(kind_id, id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/refresh")
def refresh() -> dict:
    client.clear()
    clear_schema()
    preload(resume=False)
    return {"ok": True, "region": region(), "preloaded": True}


@app.post("/api/generate")
def generate(body: GenerateBody) -> dict:
    kinds = [str(k) for k in body.kinds if k]
    objects = []
    for spec in body.objects:
        kind_id = str(spec.get("kind") or "")
        object_id = str(spec.get("id") or "")
        if kind_id and object_id:
            objects.append({"kind": kind_id, "id": object_id})
    try:
        report = generate_tf(kinds=kinds, objects=objects)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "ok": True,
        "output": report["output"],
        "selected": report["selected"],
        "expanded": report["expanded"],
        "generated": len(report["generated"]),
        "skipped": len(report["skipped"]),
        "report": report,
    }
