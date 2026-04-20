# SADE container images

## Image

- **Dockerfile:** [`Dockerfile`](Dockerfile) (build context: **repository root**).
- **Base:** `python:3.12-slim`; installs the `sade` package from `pyproject.toml` + `src/`.
- **Default process:** `uvicorn sade.api:app --host 0.0.0.0 --port 8000` (HTTP ingest on **8000**).
- **Worker (same image):** `python -m sade.decision_worker` with `REDIS_URL` set.

Build:

```bash
docker build -f docker/Dockerfile -t sade-decision:latest .
```

## Local stack (Compose)

From the repo root:

```bash
cp .env.example .env
# Edit .env: OPENAI_API_KEY, DECISION_RESULT_URL, optional SADE_INGEST_API_KEY
docker compose up --build
```

- **Ingest:** `POST http://localhost:8000/decision-request`
- **Redis** is on port **6379**; `api` and `worker` receive `REDIS_URL=redis://redis:6379/0` from Compose.

## AWS (ECS / ECR / Kubernetes)

Hand off to platform:

1. **Push** the built image to your registry (e.g. ECR).
2. **Two workloads** from the **same image**:
   - **API service:** command `uvicorn sade.api:app --host 0.0.0.0 --port 8000` (or match target group port; set `EXPOSE` / health check accordingly).
   - **Worker service:** command `python -m sade.decision_worker` (no ingress; scale horizontally with the queue).
3. **Managed Redis** (ElastiCache or similar): set `REDIS_URL` on **both** API and worker to the Redis connection string.
4. **Secrets / config** (not baked into the image):
   - `OPENAI_API_KEY`
   - `DECISION_RESULT_URL` — full URL to the platform `POST /decision-result` receiver (**required** if callbacks are desired; set on **worker** always; API only needs it if running jobs in-process without Redis).
   - Optional: `SADE_INGEST_API_KEY` / `SADE_INGEST_API_KEYS` for inbound auth on `/decision-request`.
5. **Health checks:** HTTP GET `http://<task>:8000/openapi.json` (or your ALB path) for the API; worker has no HTTP port—use process supervision or queue-depth metrics.

## Ports

| Process | Port | Notes |
|---------|------|--------|
| API (uvicorn) | 8000 | Map ALB / NLB to this port on the API service. |
| Redis | 6379 | Internal only in Compose; use private endpoint in AWS. |

## Compose file location

Root [`docker-compose.yml`](../docker-compose.yml) is for **local integration**; production typically uses the Dockerfile with orchestrator-specific YAML (ECS task defs, K8s manifests).
