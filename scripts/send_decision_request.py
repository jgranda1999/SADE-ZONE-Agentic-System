#!/usr/bin/env python3
"""
POST an entry request to the SADE ingest API (`sade.api:app`).

The API ignores per-request callback URLs; it only POSTs completed evaluations to
``DECISION_RESULT_URL``. For local end-to-end testing this script listens on a fixed
localhost port (default ``SADE_CALLBACK_PORT`` or 8765) at ``CALLBACK_PATH``. Start
**uvicorn** (and **decision_worker** if using Redis) with::

    export DECISION_RESULT_URL=http://127.0.0.1:<port>/decision-result

matching that port, then run this script so the listener is up before the worker
finishes.

Which JSON to send is chosen by passing one of the same argv tokens as before
(e.g. ``accept_entry_request``, ``action_required_entry_request``, …).

With Redis-backed ingest, idempotency keys persist in Redis (see ``SADE_IDEMPOTENCY_TTL_SEC``).
Reusing the same ``evaluation_id`` from a fixture always yields HTTP **200** after the first
successful **202**. For local testing of a **new** enqueue, pass ``--fresh`` to replace
``evaluation_id`` and ``evaluation_series_id`` with new UUIDs, or clear Redis keys / use
``redis-cli FLUSHDB`` on a dev instance.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = os.environ.get("DECISION_REQUEST_URL", "http://127.0.0.1:8000/decision-request")
CALLBACK_PATH = "/decision-result"
CALLBACK_PORT = int(os.environ.get("SADE_CALLBACK_PORT", "8765"))
WAIT_TIMEOUT = float(os.environ.get("SADE_CALLBACK_WAIT_TIMEOUT", "3600"))


def _make_callback_handler(
    expected_evaluation_id: str,
    expected_path: str,
    results: queue.Queue[dict[str, Any]],
) -> type[BaseHTTPRequestHandler]:
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            req_path = self.path.split("?", 1)[0]
            if req_path != expected_path:
                self.send_response(404)
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self.send_response(400)
                self.end_headers()
                return
            if not isinstance(data, dict):
                self.send_response(400)
                self.end_headers()
                return
            if data.get("evaluation_id") == expected_evaluation_id:
                try:
                    results.put_nowait(data)
                except queue.Full:
                    pass
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return CallbackHandler


def main() -> int:
    argv = [a for a in sys.argv[1:] if a != "--fresh"]
    fresh = "--fresh" in sys.argv[1:] or os.environ.get(
        "SADE_FRESH_EVALUATION_IDS", ""
    ).strip().lower() in ("1", "true", "yes")

    if "new_user_no_att_rr_erh" in argv:
        default_file = REPO_ROOT / "src/sade/resources/entry-requests-api/action_required_entry_request_no_att_rr_erh.json"
    elif "new_user" in argv:
        default_file = REPO_ROOT / "src/sade/resources/entry-requests-api/accept_entry_request_no_rr_erh.json"
    elif "accept_entry_request" in argv:
        default_file = REPO_ROOT / "src/sade/resources/entry-requests-api/accept_entry_request.json"
    elif "accept_entry_request_with_constraints" in argv:
        default_file = REPO_ROOT / "src/sade/resources/entry-requests-api/accept_with_contraints_entry_request.json"
    elif "action_required_entry_request" in argv:
        default_file = REPO_ROOT / "src/sade/resources/entry-requests-api/action_required_entry_request.json"
    elif "deny_entry_request" in argv:
        default_file = REPO_ROOT / "src/sade/resources/entry-requests-api/deny_entry_request.json"
    else:
        raise ValueError("Invalid entry request file")

    with default_file.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if fresh:
        payload["evaluation_id"] = str(uuid.uuid4())
        payload["evaluation_series_id"] = str(uuid.uuid4())
        print(
            "Fresh evaluation_id / evaluation_series_id (new Redis idempotency keys)",
            file=sys.stderr,
        )

    raw_eid = payload.get("evaluation_id")
    if raw_eid is None:
        print("Entry request JSON must include evaluation_id", file=sys.stderr)
        return 1
    try:
        evaluation_id = str(uuid.UUID(str(raw_eid).strip()))
    except (ValueError, TypeError):
        print("Invalid evaluation_id in JSON (expected UUID)", file=sys.stderr)
        return 1

    results: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
    handler_cls = _make_callback_handler(evaluation_id, CALLBACK_PATH, results)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", CALLBACK_PORT), handler_cls)
    except OSError as exc:
        print(
            f"Could not bind callback server to 127.0.0.1:{CALLBACK_PORT} ({exc}). "
            "Choose a free port with SADE_CALLBACK_PORT and set DECISION_RESULT_URL on the API/worker.",
            file=sys.stderr,
        )
        return 1
    serve_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    serve_thread.start()

    callback_url = f"http://127.0.0.1:{CALLBACK_PORT}{CALLBACK_PATH}"

    outbound = dict(payload)
    outbound["evaluation_id"] = evaluation_id

    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = os.environ.get("SADE_INGEST_API_KEY", "").strip()
    if key:
        headers["X-API-Key"] = key

    print(f"Callback server: {callback_url}")
    print(
        "Ensure API (and worker if using Redis) has "
        f"DECISION_RESULT_URL={callback_url}",
        file=sys.stderr,
    )
    print("Waiting for decision after ingest acceptance…")

    def _shutdown_httpd() -> None:
        try:
            httpd.shutdown()
        except Exception:
            pass
        serve_thread.join(timeout=10.0)

    def _on_sigint(_signum: int, _frame: Any) -> None:
        print("\nInterrupted.", file=sys.stderr)
        _shutdown_httpd()
        sys.exit(130)

    signal.signal(signal.SIGINT, _on_sigint)

    try:
        r = httpx.post(DEFAULT_URL, json=outbound, headers=headers, timeout=120.0)
        print(f"HTTP {r.status_code}")
        try:
            print(json.dumps(r.json(), indent=2))
        except json.JSONDecodeError:
            print(r.text)

        if r.status_code not in (200, 202):
            return 1

        decision = results.get(timeout=WAIT_TIMEOUT)
    except queue.Empty:
        print(
            f"\nTimed out after {WAIT_TIMEOUT}s waiting for decision callback.",
            file=sys.stderr,
        )
        return 124
    finally:
        _shutdown_httpd()

    print("\n=== decision callback ===")
    print(json.dumps(decision, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
