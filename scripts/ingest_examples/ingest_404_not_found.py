#!/usr/bin/env python3
"""POST to a path that does not exist — expect 404 Not Found."""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import httpx

INGEST = os.environ.get("DECISION_REQUEST_URL", "http://127.0.0.1:8000/decision-request")


def main() -> None:
    p = urlparse(INGEST)
    wrong = urlunparse((p.scheme, p.netloc, "/__no_such_route__", "", "", ""))
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("SADE_INGEST_API_KEY", "").strip()
    if key:
        headers["X-API-Key"] = key

    r = httpx.post(wrong, json={}, headers=headers, timeout=60.0)
    print(wrong)
    print(r.status_code)
    print(r.text)


if __name__ == "__main__":
    main()
