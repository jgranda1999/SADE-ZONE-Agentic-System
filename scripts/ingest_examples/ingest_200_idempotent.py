#!/usr/bin/env python3
"""POST the same evaluation twice — expect 202 then 200 (idempotent duplicate)."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_JSON = REPO / "resources/entry-requests/action_required_entry_request.json"
URL = os.environ.get("DECISION_REQUEST_URL", "http://127.0.0.1:8000/decision-request")


def main() -> None:
    with DEFAULT_JSON.open(encoding="utf-8") as f:
        body = json.load(f)
    body["evaluation_id"] = str(uuid.uuid4())
    body["evaluation_series_id"] = str(uuid.uuid4())

    headers = {"Content-Type": "application/json"}
    key = os.environ.get("SADE_INGEST_API_KEY", "").strip()
    if key:
        headers["X-API-Key"] = key

    r1 = httpx.post(URL, json=body, headers=headers, timeout=60.0)
    print("first:", r1.status_code)
    print(r1.text)
    r2 = httpx.post(URL, json=body, headers=headers, timeout=60.0)
    print("second:", r2.status_code)
    print(r2.text)


if __name__ == "__main__":
    main()
