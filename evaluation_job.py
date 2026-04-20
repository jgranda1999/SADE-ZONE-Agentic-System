"""
Run a single decision evaluation: orchestration, optional result persistence, callback POST.

Used by the HTTP API (in-process fallback) and by ``decision_worker`` when using Redis Streams.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from evaluation_api_response import (
    build_processing_failed_response,
    to_evaluation_api_payload,
)
from main import process_entry_request

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent

_TRANSIENT_CALLBACK_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_CALLBACK_ATTEMPTS = 5


async def post_decision_result_with_retries(
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


def persist_orchestrator_output_json(
    evaluation_id: str,
    output: Optional[Dict[str, Any]],
) -> None:
    """Write orchestrator contract JSON when ``SADE_PERSIST_RESULTS`` is enabled."""
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


async def run_evaluation_job(
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

    persist_orchestrator_output_json(evaluation_id, output)
    await post_decision_result_with_retries(payload, decision_result_url)
