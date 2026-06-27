# Top-K MVP — Deploy Guide

## Prerequisites

- Docker Engine 24+ and Docker Compose v2
- `make` (optional, for convenience targets)
- Port 8010 available on the host (configurable via `APP_PORT`)

## Quick Start

```bash
# 1. Clone and enter the repo
cd sd-top-k-backend-mvp

# 2. Configure environment (optional — defaults work for local dev)
cp .env.example .env
# Edit .env if you need different DB credentials or ports

# 3. Build and start all services
docker compose up -d --build

# 4. Wait for the app to be healthy
#    (compose healthcheck probes /healthz every 10s, up to 30 retries)
docker compose ps   # look for "healthy" on the `app` service

# 5. Verify
curl -s http://localhost:8010/healthz | python -m json.tool
```

Expected output:

```json
{
    "status": "healthy",
    "redis": "connected",
    "postgres": "connected",
    "window": "1h",
    "uptime_seconds": 2.345
}
```

## Running Acceptance Tests

```bash
# Install test dependencies (one-time)
pip install httpx pytest

# Source the manifest and run
set -a && . verify/manifest.env && set +a
pytest verify/acceptance/ -v
```

Or with the manifest variables inline:

```bash
PORT=8010 API_BASE_URL=http://localhost:8010 pytest verify/acceptance/ -v
```

## Service Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/healthz` | GET | Health check (DB + Redis) |
| `/events` | POST | Ingest a batch of events |
| `/trending` | GET | Retrieve top-K trending items |
| `/trending/count?item_id=X` | GET | Point-query approximate count |
| `/admin/blacklist` | POST | Add items to blacklist |
| `/admin/blacklist` | DELETE | Remove items from blacklist |
| `/admin/reset` | POST | Reset in-memory state |

All endpoints are served on the host at `http://localhost:${APP_PORT:-8010}`.

## Architecture

```
┌─────────────────────────────────────────┐
│  Host (port 8010)                       │
│  ┌──────────┐  ┌──────┐  ┌───────────┐ │
│  │   app    │  │  db  │  │   redis   │ │
│  │  :8000   │  │ :5432│  │   :6379   │ │
│  │ FastAPI  │  │ PG16 │  │ Redis 7   │ │
│  └──────────┘  └──────┘  └───────────┘ │
│       │            │           │        │
│       └────────────┴───────────┘        │
│          compose network                │
└─────────────────────────────────────────┘
```

- **app** — FastAPI on Python 3.12-slim, multi-stage Docker build. Publishes `${APP_PORT:-8010}:8000`.
- **db** — PostgreSQL 16 Alpine. Not published to host (compose-internal only).
- **redis** — Redis 7 Alpine. Not published to host (compose-internal only).

Services communicate over the default compose network. Only `app` is reachable from the host.

## Configuration

All settings are read from environment variables. See `.env.example` for the full list.

Key variables:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://topk:topk@localhost:5432/topk` | PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `APP_PORT` | `8000` | Port the FastAPI server listens on (in-container) |
| `CMS_EPSILON` | `0.01` | Count-Min Sketch accuracy parameter |
| `CMS_DELTA` | `0.001` | Count-Min Sketch confidence parameter |
| `SPACE_SAVING_CAPACITY` | `1000` | Space-Saving tracker capacity |
| `BLOOM_CAPACITY` | `10000000` | Bloom filter expected item count |
| `BLOOM_ERROR_RATE` | `0.001` | Bloom filter false-positive rate |
| `REFRESH_INTERVAL_SECONDS` | `30` | Trending cache refresh interval |

For compose, the `DATABASE_URL` and `REDIS_URL` are overridden in `docker-compose.yml` to use compose service names (`db`, `redis`). No action needed.

## Healthchecks

All three services have healthchecks:

- **db**: `pg_isready -U topk` (every 5s)
- **redis**: `redis-cli ping` (every 5s)
- **app**: `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"` (every 10s, 30 retries)

`app` depends on `db` and `redis` with `condition: service_healthy`.

## Database Migrations

Migrations run automatically on app startup via Alembic (`alembic upgrade head`). The migration engine is invoked as a subprocess from `create_app()` so it gets a clean event loop (Alembic's async `env.py` uses `asyncio.run()` which cannot nest inside uvicorn's loop).

If you need to run a migration manually:

```bash
docker compose run --rm app alembic upgrade head
```

Or to create a new migration:

```bash
docker compose run --rm app alembic revision --autogenerate -m "description"
```

## Logs

```bash
# Tail all services
docker compose logs -f --tail=100

# Specific service
docker compose logs -f app
```

## Teardown

```bash
# Stop and remove containers, networks (keeps volumes)
docker compose down

# Stop and remove EVERYTHING including volumes
docker compose down -v
```

## Troubleshooting

### App fails to start — "Connection refused" to db/redis

The app waits for `db` and `redis` to report healthy before starting. Check:

```bash
docker compose ps
```

If db or redis show "unhealthy", check their logs:

```bash
docker compose logs db
docker compose logs redis
```

### Port 8010 already in use

Set a different port:

```bash
APP_PORT=8011 docker compose up -d --build
```

### Redis unavailable warning

The app starts in degraded mode if Redis is unreachable. The health endpoint returns `"redis": "disconnected"` but status code 200 (healthy) as long as PostgreSQL is up. Top-K queries will return empty/stale results without Redis.

### Database migration failures

Check the app logs for alembic errors:

```bash
docker compose logs app | grep -i alembic
```

If migrations are stuck, you can reset the database:

```bash
docker compose down -v
docker compose up -d --build
```
