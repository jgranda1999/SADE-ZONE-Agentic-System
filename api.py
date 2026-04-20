"""
Async HTTP ingest: POST /decision-request accepts a full entry request, returns 202 immediately,
runs process_entry_request in the background (or enqueues to Redis Streams when ``REDIS_URL`` is set),
POSTs the result to DECISION_RESULT_URL or an optional
per-request ``decision_result_url`` (stripped from the body before orchestration).

HTTP semantics (summary):
- **400** — malformed JSON, non-object body, or payload rejected by validation / business rules
  (including invalid UUIDs and idempotency conflicts on ``evaluation_id``).
- **401** — authentication required (``SADE_INGEST_API_KEY*`` configured) but missing or invalid
  credentials (``Authorization: Bearer`` or ``X-API-Key``).
- **403** — caller presented a credential that is **explicitly revoked** (``SADE_INGEST_REVOKED_KEYS``).
- **404** — no route for the URL path (unknown path, trailing-slash mismatch, etc.).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, FrozenSet, Optional
from urllib.parse import urlparse

from fastapi import Body, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from evaluation_job import run_evaluation_job
from queue_redis import EnqueueOutcome, enqueue_decision_request

logger = logging.getLogger(__name__)

# In-memory idempotency when ``REDIS_URL`` is not set (single-process only)
_jobs_lock = asyncio.Lock()
_jobs: Dict[str, Dict[str, str]] = {}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    redis_url = (os.environ.get("REDIS_URL") or "").strip()
    if redis_url:
        import redis.asyncio as redis_mod

        client = redis_mod.from_url(redis_url, decode_responses=True)
        app.state.redis = client
        logger.info("Redis connected for decision queue (%s)", redis_mod.__name__)
        yield
        await client.aclose()
        logger.info("Redis connection closed")
    else:
        yield


app = FastAPI(title="SADE Decision Ingest", version="0.1.0", lifespan=_lifespan)


def _load_allowed_api_keys() -> Optional[FrozenSet[str]]:
    """If non-empty, POST /decision-request requires ``X-API-Key`` or ``Authorization: Bearer``."""
    raw = (os.environ.get("SADE_INGEST_API_KEYS") or "").strip()
    if not raw:
        raw = (os.environ.get("SADE_INGEST_API_KEY") or "").strip()
    if not raw:
        return None
    keys = frozenset(k.strip() for k in raw.split(",") if k.strip())
    return keys if keys else None


def _load_revoked_api_keys() -> FrozenSet[str]:
    raw = (os.environ.get("SADE_INGEST_REVOKED_KEYS") or "").strip()
    if not raw:
        return frozenset()
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


def _extract_api_token(request: Request) -> Optional[str]:
    x = request.headers.get("x-api-key")
    if x and str(x).strip():
        return str(x).strip()
    auth = request.headers.get("authorization")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            tok = parts[1].strip()
            return tok or None
    return None


@app.middleware("http")
async def _ingest_api_key_middleware(request: Request, call_next):
    """Require API key for POST /decision-request when ``SADE_INGEST_API_KEY*`` is set."""
    if request.url.path != "/decision-request" or request.method.upper() != "POST":
        return await call_next(request)

    allowed = _load_allowed_api_keys()
    if allowed is None:
        return await call_next(request)

    token = _extract_api_token(request)
    revoked = _load_revoked_api_keys()

    if token and token in revoked:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "API key revoked"},
        )

    if not token:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Authentication required"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    if token not in allowed:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid API key"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def _request_validation_400(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Map body / parameter validation failures to **400** (invalid client payload)."""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request body", "errors": exc.errors()},
    )


@app.exception_handler(StarletteHTTPException)
async def _starlette_http_exception(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    if exc.status_code == status.HTTP_404_NOT_FOUND:
        detail = exc.detail
        if isinstance(detail, str):
            body_detail: Any = detail if detail else "Not Found"
        else:
            body_detail = detail
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": body_detail},
        )
    return await http_exception_handler(request, exc)


def _require_uuid(value: Any, field: str) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing or empty {field}",
        )
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid UUID for {field}",
        )


def _acceptance_body(evaluation_id: str, evaluation_series_id: str) -> Dict[str, str]:
    return {
        "status": "ACCEPTED",
        "evaluation_id": evaluation_id,
        "evaluation_series_id": evaluation_series_id,
    }


def _normalize_decision_result_url(raw: object) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="decision_result_url must be a non-empty string when provided",
        )
    url = raw.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="decision_result_url must use http or https",
        )
    return url


@app.post("/decision-request")
async def decision_request(
    request: Request,
    body: Dict[str, object] = Body(...),
) -> JSONResponse:
    evaluation_id = _require_uuid(body.get("evaluation_id"), "evaluation_id")
    evaluation_series_id = _require_uuid(
        body.get("evaluation_series_id"),
        "evaluation_series_id",
    )

    body = dict(body)
    decision_result_url = _normalize_decision_result_url(body.pop("decision_result_url", None))
    body["evaluation_id"] = evaluation_id
    body["evaluation_series_id"] = evaluation_series_id

    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        outcome = await enqueue_decision_request(
            redis_client,
            body,
            decision_result_url,
        )
        if outcome == EnqueueOutcome.CONFLICT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "evaluation_id already accepted with a different evaluation_series_id"
                ),
            )
        if outcome == EnqueueOutcome.DUPLICATE:
            logger.info(
                "Idempotent replay evaluation_id=%s (Redis key exists; no new stream message)",
                evaluation_id,
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=_acceptance_body(evaluation_id, evaluation_series_id),
            )
        logger.info(
            "Accepted new evaluation_id=%s (enqueued to Redis stream)",
            evaluation_id,
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=_acceptance_body(evaluation_id, evaluation_series_id),
        )

    async with _jobs_lock:
        existing = _jobs.get(evaluation_id)
        if existing:
            if existing["evaluation_series_id"] != evaluation_series_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "evaluation_id already accepted with a different evaluation_series_id"
                    ),
                )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=_acceptance_body(evaluation_id, evaluation_series_id),
            )

        _jobs[evaluation_id] = {
            "evaluation_id": evaluation_id,
            "evaluation_series_id": evaluation_series_id,
        }

    asyncio.create_task(run_evaluation_job(body, decision_result_url))

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=_acceptance_body(evaluation_id, evaluation_series_id),
    )
