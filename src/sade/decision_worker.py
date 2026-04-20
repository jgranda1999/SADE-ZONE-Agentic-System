"""
Dedicated consumer for the Redis Streams decision queue.

Requires ``REDIS_URL``. Run alongside the FastAPI app (separate process/container).

Example::

    export REDIS_URL=redis://localhost:6379/0
    python -m sade.decision_worker
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

import redis.asyncio as redis

from sade.evaluation_job import run_evaluation_job
from sade.queue_redis import default_consumer_name, worker_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)


def _install_shutdown_waits(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        if stop.is_set():
            logger.warning("Second shutdown signal: exiting immediately")
            raise SystemExit(130)
        logger.info(
            "Shutdown signal received; will exit after the current batch completes "
            "(press Ctrl+C again to force quit)"
        )
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            # Windows event loop may not support signal handlers
            return


async def _main() -> None:
    url = (os.environ.get("REDIS_URL") or "").strip()
    if not url:
        print("REDIS_URL is required", file=sys.stderr)
        raise SystemExit(1)

    stop = asyncio.Event()
    _install_shutdown_waits(stop)

    client = redis.from_url(url, decode_responses=True)
    try:
        await worker_loop(
            client,
            default_consumer_name(),
            run_evaluation_job,
            stop_event=stop,
        )
    finally:
        await client.aclose()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
