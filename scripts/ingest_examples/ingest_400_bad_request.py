#!/usr/bin/env python3
"""POST a non-object JSON body — expect 400 Bad Request."""

from __future__ import annotations

import os

import httpx

URL = os.environ.get("DECISION_REQUEST_URL", "http://127.0.0.1:8000/decision-request")

HEADERS = {"Content-Type": "application/json"}
key = os.environ.get("SADE_INGEST_API_KEY", "").strip()
if key:
    HEADERS["X-API-Key"] = key


def main() -> None:
    r = httpx.post(URL, content=b"[1,2,3]", headers=HEADERS, timeout=60.0)
    print(r.status_code)
    print(r.text)


if __name__ == "__main__":
    main()
