#!/usr/bin/env python3
"""
POST an entry request to the SADE ingest API (api.py).

Starts a small local HTTP server, adds ``decision_result_url`` to the payload,
POSTs to ``/decision-request``, then blocks until the API POSTs the finished evaluation
payload back to this process.

Which JSON to send is chosen by passing one of the same argv tokens as before
(e.g. ``accept_entry_request``, ``action_required_entry_request``, …).
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
    if "new_user_no_att_rr_erh" in sys.argv:
        default_file = REPO_ROOT / "resources/entry-requests-api/action_required_entry_request_no_att_rr_erh.json"
    elif "new_user" in sys.argv:
        default_file = REPO_ROOT / "resources/entry-requests-api/accept_entry_request_no_rr_erh.json"
    elif "accept_entry_request" in sys.argv:
        default_file = REPO_ROOT / "resources/entry-requests-api/accept_entry_request.json"
    elif "accept_entry_request_with_constraints" in sys.argv:
        default_file = REPO_ROOT / "resources/entry-requests-api/accept_with_contraints_entry_request.json"
    elif "action_required_entry_request" in sys.argv:
        default_file = REPO_ROOT / "resources/entry-requests-api/action_required_entry_request.json"
    elif "deny_entry_request" in sys.argv:
        default_file = REPO_ROOT / "resources/entry-requests-api/deny_entry_request.json"
    else:
        raise ValueError("Invalid entry request file")

    with default_file.open("r", encoding="utf-8") as f:
        payload = json.load(f)

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
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    serve_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    serve_thread.start()

    _, port = httpd.server_address[:2]
    callback_url = f"http://127.0.0.1:{port}{CALLBACK_PATH}"

    outbound = dict(payload)
    outbound["evaluation_id"] = evaluation_id
    outbound["decision_result_url"] = callback_url

    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = os.environ.get("SADE_INGEST_API_KEY", "").strip()
    if key:
        headers["X-API-Key"] = key

    print(f"Callback server: {callback_url}")
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
