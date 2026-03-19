"""FastAPI app entry point (routes live here; query logic lives in `app/search.py`)."""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from app.models import engine, get_session
from app.search import run_search

# Maximum allowed query length for `/search?q=...`.
# This prevents overly long inputs from wasting CPU/DB time (v1 safeguard).
MAX_QUERY_LENGTH = 64

# Templates and static files are under the app package.
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI()

# Serve CSS and other static assets at /static.
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _get_log_level() -> int:
    """Parse LOG_LEVEL env var with a safe default."""
    raw = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, raw, logging.INFO)


def _configure_logging() -> logging.Logger:
    """Configure console logging suitable for Railway stdout/stderr."""
    logger = logging.getLogger("slipwords")
    logger.setLevel(_get_log_level())
    logger.propagate = False

    # Debugging/logging logic: ensure we don't attach duplicate handlers.
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


logger = _configure_logging()


def _request_id_from_request(request: Request) -> str:
    """Return the request correlation id for this request."""
    return cast(str, getattr(request.state, "request_id", "unknown"))


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log request/response metadata and correlate errors with request ids."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        """Log method/path/status/latency and return a request-id on errors."""
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id

        # Avoid log spam from static asset requests.
        if request.url.path.startswith("/static"):
            return await call_next(request)

        start = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start) * 1000.0

            # Keep access logs lightweight; avoid logging full user query text.
            if request.url.path == "/search":
                q_val = request.query_params.get("q", "")
                q_len = len(q_val)
                logger.info(
                    "access request_id=%s method=%s path=%s q_len=%d status=%s duration_ms=%.2f",
                    request_id,
                    request.method,
                    request.url.path,
                    q_len,
                    response.status_code,
                    duration_ms,
                )
            else:
                logger.info(
                    "access request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
                    request_id,
                    request.method,
                    request.url.path,
                    response.status_code,
                    duration_ms,
                )

            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000.0
            if isinstance(exc, HTTPException):
                logger.info(
                    "http_exception request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
                    request_id,
                    request.method,
                    request.url.path,
                    exc.status_code,
                    duration_ms,
                )
                raise
            logger.exception(
                "request_error request_id=%s method=%s path=%s duration_ms=%.2f",
                request_id,
                request.method,
                request.url.path,
                duration_ms,
            )

            body = f"Internal Server Error\nRequest ID: {request_id}\n"
            response = HTMLResponse(status_code=500, content=body)
            response.headers["X-Request-ID"] = request_id
            return response


app.add_middleware(RequestLoggingMiddleware)


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Render the homepage with search bar and short about text."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = "",
    db: Session = Depends(get_session),
) -> HTMLResponse:
    """
    Run search using query string `q`. Detection and query logic are in search.py.
    Renders the results template with results and query for display.
    Empty or whitespace-only queries redirect back to the homepage.
    """
    request_id = _request_id_from_request(request)
    if not q or not q.strip():
        return RedirectResponse(url="/", status_code=303)

    q = q.strip()
    if len(q) > MAX_QUERY_LENGTH:
        # No custom error page in v1; redirect keeps the UX simple.
        return RedirectResponse(url="/", status_code=303)

    results, query_type = run_search(db, q)
    logger.info(
        "search request_id=%s query_type=%s results=%d q_len=%d",
        request_id,
        query_type,
        len(results),
        len(q),
    )
    return templates.TemplateResponse(
        "results.html",
        {
            "request": request,
            "query": q,
            "results": results,
            "query_type": query_type,
        },
    )


@app.on_event("startup")
def on_startup() -> None:
    """Log startup for visibility on Railway."""
    logger.info("startup LOG_LEVEL=%s", logging.getLevelName(logger.level))


@app.get("/healthz", response_class=JSONResponse)
def healthz() -> dict[str, str]:
    """Return liveness status without touching the database."""
    return {"status": "ok"}


@app.get("/readyz", response_class=JSONResponse)
def readyz() -> JSONResponse:
    """Return readiness based on Postgres connectivity."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return JSONResponse(status_code=200, content={"status": "ready"})
    except Exception:
        logger.exception("readyz db_check_failed")
        return JSONResponse(
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready"},
        )
