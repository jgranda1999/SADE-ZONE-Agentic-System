"""
Redis Streams queue for decision requests: atomic enqueue with idempotency, worker read loop.

Requires ``redis`` (async). Configure with ``REDIS_URL`` and optional ``SADE_*`` env vars.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class EnqueueOutcome(str, Enum):
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    ENQUEUED = "enqueued"


def stream_key() -> str:
    return (os.environ.get("SADE_STREAM_KEY") or "sade:decisions").strip()


def consumer_group() -> str:
    return (os.environ.get("SADE_CONSUMER_GROUP") or "sade-workers").strip()


def idempotency_key(evaluation_id: str) -> str:
    prefix = (os.environ.get("SADE_IDEMPOTENCY_PREFIX") or "sade:ingest").strip().rstrip(":")
    return f"{prefix}:{evaluation_id}"


def idempotency_ttl_sec() -> int:
    raw = (os.environ.get("SADE_IDEMPOTENCY_TTL_SEC") or "").strip()
    if raw:
        try:
            return max(60, int(raw))
        except ValueError:
            pass
    return 86400 * 30


def read_block_ms() -> int:
    raw = (os.environ.get("SADE_STREAM_BLOCK_MS") or "").strip()
    if raw:
        try:
            return max(1000, int(raw))
        except ValueError:
            pass
    return 5000


def default_consumer_name() -> str:
    raw = (os.environ.get("SADE_CONSUMER_NAME") or "").strip()
    if raw:
        return raw
    return f"{socket.gethostname()}-{os.getpid()}"


async def ensure_consumer_group(
    redis: Any,
    sk: Optional[str] = None,
    group: Optional[str] = None,
) -> None:
    """Create the stream and consumer group if they do not exist."""
    sk = sk or stream_key()
    group = group or consumer_group()
    try:
        await redis.xgroup_create(sk, group, id="0", mkstream=True)
        logger.info("Created Redis stream %r group %r", sk, group)
    except Exception as exc:  # noqa: BLE001
        if "BUSYGROUP" in str(exc) or "BUSYGROUP" in repr(exc):
            return
        # redis-py raises ResponseError
        err_name = type(exc).__name__
        if err_name == "ResponseError" and "BUSYGROUP" in str(exc):
            return
        raise


async def enqueue_decision_request(
    redis: Any,
    entry_request: Dict[str, Any],
) -> EnqueueOutcome:
    """
    Enqueue a job or report duplicate / ``evaluation_series_id`` conflict.

    Uses ``SET idempotency_key NX`` **before** ``XADD``to fix this issue: 
        Ordering ``XADD`` first allowed workers to read duplicate stream entries before the handler could
        ``XDEL`` them, so duplicate POSTs could still run.

    ``entry_request`` must include ``evaluation_id`` and ``evaluation_series_id``.

    The idempotency key is stored in Redis until ``SADE_IDEMPOTENCY_TTL_SEC`` (default 30 days).
    Re-posting the same ``evaluation_id`` after a successful accept therefore returns DUPLICATE
    without a new stream message — HTTP **200**, not **202**.
    """
    evaluation_id = str(entry_request["evaluation_id"])
    evaluation_series_id = str(entry_request["evaluation_series_id"])
    sk = stream_key()
    ikey = idempotency_key(evaluation_id)
    ttl = idempotency_ttl_sec()
    payload = json.dumps(entry_request, separators=(",", ":"), ensure_ascii=False)

    ok = await redis.set(ikey, evaluation_series_id, nx=True, ex=ttl)
    if not ok:
        existing = await redis.get(ikey)
        if existing is None:
            logger.warning(
                "Idempotency key %s missing after SET NX failure; evaluation_id=%s",
                ikey,
                evaluation_id,
            )
            return EnqueueOutcome.CONFLICT
        if isinstance(existing, bytes):
            existing = existing.decode()
        if existing == evaluation_series_id:
            return EnqueueOutcome.DUPLICATE
        return EnqueueOutcome.CONFLICT

    try:
        msg_id = await redis.xadd(sk, {"payload": payload})
    except Exception:
        await redis.delete(ikey)
        raise

    if isinstance(msg_id, bytes):
        msg_id = msg_id.decode()

    logger.info(
        "Enqueued evaluation_id=%s to stream %s msg_id=%s",
        evaluation_id,
        sk,
        msg_id,
    )
    return EnqueueOutcome.ENQUEUED


async def process_one_batch(
    redis: Any,
    consumer_name: str,
    run_job: Callable[[Dict[str, Any]], Awaitable[None]],
    sk: Optional[str] = None,
    group: Optional[str] = None,
) -> bool:
    """
    Read up to one message from the stream, run the job, XACK on completion.

    Returns True if a message was processed, False if none available (timeout).
    """
    sk = sk or stream_key()
    group = group or consumer_group()
    block = read_block_ms()

    streams = await redis.xreadgroup(
        groupname=group,
        consumername=consumer_name,
        streams={sk: ">"},
        count=1,
        block=block if block > 0 else None,
    )
    if not streams:
        return False

    _skey, messages = streams[0]
    if not messages:
        return False

    msg_id, fields = messages[0]
    if isinstance(msg_id, bytes):
        msg_id = msg_id.decode()

    raw_payload = fields.get("payload") or fields.get(b"payload")

    if isinstance(raw_payload, bytes):
        raw_payload = raw_payload.decode("utf-8")

    try:
        entry = json.loads(raw_payload)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error("Invalid JSON in stream message %s: %s", msg_id, exc)
        await redis.xack(sk, group, msg_id)
        return True

    eid = entry.get("evaluation_id", msg_id)
    logger.info("Processing stream message %s evaluation_id=%s", msg_id, eid)
    try:
        await run_job(entry)
    except Exception:
        logger.exception("Worker job failed for msg_id=%s evaluation_id=%s", msg_id, eid)
    finally:
        await redis.xack(sk, group, msg_id)

    return True


async def worker_loop(
    redis: Any,
    consumer_name: str,
    run_job: Callable[[Dict[str, Any]], Awaitable[None]],
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Block until ``stop_event`` is set (if given) or forever: one job at a time."""
    await ensure_consumer_group(redis)
    sk = stream_key()
    group = consumer_group()
    logger.info(
        "Worker started consumer=%r stream=%r group=%r",
        consumer_name,
        sk,
        group,
    )
    while True:
        if stop_event is not None and stop_event.is_set():
            logger.info("Worker loop stopping (shutdown requested)")
            break
        await process_one_batch(redis, consumer_name, run_job, sk=sk, group=group)
