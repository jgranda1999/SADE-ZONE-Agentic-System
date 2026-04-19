#!/usr/bin/env python3
"""POST without X-API-Key — expect 401 when the API has SADE_INGEST_API_KEY(S) set."""

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

    r = httpx.post(
        URL,
        json=body,
        headers={"Content-Type": "application/json"},
        timeout=60.0,
    )
    print(r.status_code)
    print(r.text)


if __name__ == "__main__":
    main()
