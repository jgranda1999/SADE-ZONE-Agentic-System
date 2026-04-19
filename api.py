"""
Async HTTP ingest: POST /decision-request accepts a full entry request, returns 202 immediately,
runs process_entry_request in the background, POSTs the result to DECISION_RESULT_URL or an optional
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
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional
from urllib.parse import urlparse

import httpx
from fastapi import Body, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from evaluation_api_response import (
    build_processing_failed_response,
    to_evaluation_api_payload,
)
from main import process_entry_request

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent

app = FastAPI(title="SADE Decision Ingest", version="0.1.0")

_TRANSIENT_CALLBACK_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_CALLBACK_ATTEMPTS = 5

# evaluation_id -> canonical acceptance metadata (idempotency; single-process only)
_jobs_lock = asyncio.Lock()
_jobs: Dict[str, Dict[str, str]] = {}


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


async def _post_decision_result_with_retries(
    payload: Dict[str, Any],
    decision_result_url: Optional[str] = None,
) -> None:
    url = (decision_result_url or "").strip() or os.environ.get("DECISION_RESULT_URL")
    if not url:
        logger.info("No decision result URL; payload: %s", payload)
        return

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=30.0)) as client:
        for attempt in range(1, _MAX_CALLBACK_ATTEMPTS + 1):
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code in _TRANSIENT_CALLBACK_STATUS:
                    logger.warning(
                        "Callback attempt %s/%s transient HTTP %s",
                        attempt,
                        _MAX_CALLBACK_ATTEMPTS,
                        resp.status_code,
                    )
                elif resp.is_success:
                    return
                else:
                    logger.error(
                        "Callback non-retryable failure: HTTP %s — %s",
                        resp.status_code,
                        (resp.text or "")[:500],
                    )
                    return
            except httpx.RequestError as exc:
                logger.warning(
                    "Callback attempt %s/%s transport error: %s",
                    attempt,
                    _MAX_CALLBACK_ATTEMPTS,
                    exc,
                )

            if attempt < _MAX_CALLBACK_ATTEMPTS:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))

    logger.error(
        "Callback exhausted retries for evaluation_id=%s",
        payload.get("evaluation_id"),
    )


def _persist_orchestrator_output_json(
    evaluation_id: str,
    output: Optional[Dict[str, Any]],
) -> None:
    """Write orchestrator contract JSON: ``{"decision": ..., "visibility": ...}`` (same shape as in CLI ``entry_result_*.txt``)."""
    flag = os.environ.get("SADE_PERSIST_RESULTS", "1").strip().lower()
    if flag in ("0", "false", "no"):
        return
    if output is None:
        logger.info(
            "No orchestrator output to persist for evaluation_id=%s (processing failed before final JSON)",
            evaluation_id,
        )
        return

    out_dir = _REPO_ROOT / "results" / "api-integration"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"entry_result_{evaluation_id}.json"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    logger.info(
        "Wrote orchestrator output %s for evaluation_id=%s",
        json_path.name,
        evaluation_id,
    )


async def _run_evaluation_job(
    entry_request: Dict[str, Any],
    decision_result_url: Optional[str] = None,
) -> None:
    evaluation_id = str(entry_request["evaluation_id"])
    evaluation_series_id = str(entry_request["evaluation_series_id"])
    output: Optional[Dict[str, Any]] = None
    try:
        output = await process_entry_request(entry_request)
        payload = to_evaluation_api_payload(
            output,
            evaluation_id,
            evaluation_series_id,
        )
    except ValueError as exc:
        payload = build_processing_failed_response(
            evaluation_id,
            evaluation_series_id,
            reason=str(exc)[:500],
        )
    except Exception as exc:  # noqa: BLE001 — last-resort processing_failed for unexpected errors
        logger.exception("Orchestration failed for evaluation_id=%s", evaluation_id)
        payload = build_processing_failed_response(
            evaluation_id,
            evaluation_series_id,
            reason=str(exc)[:500],
        )

    _persist_orchestrator_output_json(evaluation_id, output)
    await _post_decision_result_with_retries(payload, decision_result_url)


@app.post("/decision-request")
async def decision_request(
    body: Dict[str, object] = Body(...),
) -> JSONResponse:
    evaluation_id = _require_uuid(body.get("evaluation_id"), "evaluation_id")
    evaluation_series_id = _require_uuid(
        body.get("evaluation_series_id"),
        "evaluation_series_id",
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

    body = dict(body)
    decision_result_url = _normalize_decision_result_url(body.pop("decision_result_url", None))
    body["evaluation_id"] = evaluation_id
    body["evaluation_series_id"] = evaluation_series_id

    asyncio.create_task(_run_evaluation_job(body, decision_result_url))

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=_acceptance_body(evaluation_id, evaluation_series_id),
    )
