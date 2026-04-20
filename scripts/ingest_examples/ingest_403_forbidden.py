#!/usr/bin/env python3
"""
POST with X-API-Key — expect 403 when that key is in SADE_INGEST_REVOKED_KEYS on the server.

Set the key to send: export SADE_INGEST_API_KEY=your-key
(Server must also list the same key in SADE_INGEST_REVOKED_KEYS.)
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_JSON = (
    REPO
    / "src/sade/resources/entry-requests-files/entry-requests/action_required_entry_request.json"
)
URL = os.environ.get("DECISION_REQUEST_URL", "http://127.0.0.1:8000/decision-request")


def main() -> None:
    key = os.environ.get("SADE_INGEST_API_KEY", "").strip()
    if not key:
        print("Set SADE_INGEST_API_KEY to the key you revoked on the server.", file=sys.stderr)
        sys.exit(1)

    with DEFAULT_JSON.open(encoding="utf-8") as f:
        body = json.load(f)
    body["evaluation_id"] = str(uuid.uuid4())
    body["evaluation_series_id"] = str(uuid.uuid4())

    r = httpx.post(
        URL,
        json=body,
        headers={"Content-Type": "application/json", "X-API-Key": key},
        timeout=60.0,
    )
    print(r.status_code)
    print(r.text)


if __name__ == "__main__":
    main()
