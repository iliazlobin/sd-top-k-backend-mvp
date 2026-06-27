![Lint](https://github.com/iliazlobin/sd-top-k-backend-mvp/actions/workflows/lint.yml/badge.svg) ![CI](https://github.com/iliazlobin/sd-top-k-backend-mvp/actions/workflows/ci.yml/badge.svg) ![Functional](https://github.com/iliazlobin/sd-top-k-backend-mvp/actions/workflows/functional.yml/badge.svg)

# Top-K Trending API

A Top-K Heavy Hitters service that tracks approximate item frequencies over a 1-hour sliding window using Count-Min Sketch and Space-Saving algorithms. Built with FastAPI, PostgreSQL, and Redis, it accepts batched event ingestion, returns ranked top-K trending items, and defends against per-user gaming with Bloom filter deduplication. Packaged with Docker Compose for one-command deployment.

## Quickstart

Requires Docker Engine 24+ and Docker Compose v2.

```bash
# Clone and enter
git clone https://github.com/iliazlobin/sd-top-k-backend-mvp.git
cd sd-top-k-backend-mvp

# Copy and edit environment (defaults work for local dev)
cp .env.example .env

# Start all services
docker compose up -d --build

# Verify health
curl -s http://localhost:8010/healthz | python -m json.tool
```

Expected health output:

```json
{
    "status": "healthy",
    "redis": "connected",
    "postgres": "connected",
    "window": "1h",
    "uptime_seconds": 2.345
}
```

Ingest events and query trending:

```bash
# Ingest 3 events
curl -s -X POST http://localhost:8010/events \
  -H "Content-Type: application/json" \
  -d '{"events":[{"item_id":"cat_video","event_type":"view","timestamp":1719876543000}]}'

# Get top 10 trending
curl -s "http://localhost:8010/trending?window=1h&k=10" | python -m json.tool

# Point query a specific item
curl -s "http://localhost:8010/count?item_id=cat_video&window=1h" | python -m json.tool
```

## API Reference

| Method | Path | Purpose | Example |
|--------|------|---------|---------|
| `POST` | `/events` | Ingest a batch of up to 100 events | `curl -X POST .../events -d '{"events":[{"item_id":"a","event_type":"view","timestamp":1719876543000}]}'` |
| `GET` | `/trending?window=1h&k=100` | Return top-K trending items | `curl ".../trending?window=1h&k=10"` |
| `GET` | `/count?item_id=X&window=1h` | Point-query CMS estimate for a single item | `curl ".../count?item_id=cat_video&window=1h"` |
| `GET` | `/healthz` | Health check (DB + Redis connectivity) | `curl .../healthz` |
| `POST` | `/admin/blacklist` | Add items to the blacklist | `curl -X POST .../admin/blacklist -d '{"item_ids":["spam1"]}'` |
| `GET` | `/admin/blacklist` | List all blacklisted item IDs | `curl .../admin/blacklist` |
| `DELETE` | `/admin/blacklist` | Remove items from the blacklist | `curl -X DELETE .../admin/blacklist -d '{"item_ids":["spam1"]}'` |
| `POST` | `/admin/reset` | Reset all in-memory state and clear Redis caches | `curl -X POST .../admin/reset` |

All endpoints are served at `http://localhost:${APP_PORT:-8010}`.

### Request/Response Schemas

**POST /events** — accepts `{events: [{item_id, event_type, timestamp, event_id?, user_id?}]}`, returns `{accepted, duplicates, blocked}` with status 202.

**GET /trending** — returns `{items: [{item_id, count, rank}], window, k, updated_at}`.

**GET /count** — returns `{item_id, window, count, is_approximate: true}`.

**GET /healthz** — returns `{status, redis, postgres, window, uptime_seconds}`.

**POST /admin/blacklist** — accepts `{item_ids: [...]}`, returns `{added, item_ids}` with status 201.

**GET /admin/blacklist** — returns `{item_ids, count}`.

**DELETE /admin/blacklist** — accepts `{item_ids: [...]}`, returns `{removed, item_ids}`.

## Configuration

All settings are read from environment variables. See `.env.example` for the full list.

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://topk:topk@localhost:5432/topk` | PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `APP_PORT` | `8000` | In-container port FastAPI listens on |
| `CMS_EPSILON` | `0.01` | Count-Min Sketch accuracy (max overcount ratio) |
| `CMS_DELTA` | `0.001` | Count-Min Sketch confidence (1 − δ guarantee) |
| `SPACE_SAVING_CAPACITY` | `1000` | Space-Saving monitored candidates |
| `BLOOM_CAPACITY` | `10000000` | Bloom filter expected insertions |
| `BLOOM_ERROR_RATE` | `0.001` | Bloom filter false-positive rate |
| `REFRESH_INTERVAL_SECONDS` | `30` | Trending cache refresh interval |

In Docker Compose, `DATABASE_URL` and `REDIS_URL` are automatically overridden to use compose service names (`db`, `redis`). No manual configuration is needed for the compose path.

## Testing

### Unit Tests

Pure Python tests for Count-Min Sketch, Space-Saving, and Bloom filter — no external services required.

```bash
pip install ".[dev]"
pytest tests/unit/ -v
```

### Functional Tests

In-process tests with mocked Redis — exercises the full pipeline (validation, dedup, blacklist, ingestion flow, trending retrieval).

```bash
pytest tests/functional/ -v
```

### Acceptance Tests (Black-Box)

Black-box HTTP tests against a running instance — one file per functional requirement. Requires the app to be running with PostgreSQL and Redis.

```bash
# With compose running on port 8010:
PORT=8010 API_BASE_URL=http://localhost:8010 pytest verify/acceptance/ -v
```

### CI

Three workflows run on every push, PR, and daily schedule:

- **Lint** — ruff check + format
- **CI** — unit tests (PostgreSQL + Redis service containers) + full e2e acceptance with Docker Compose
- **Functional** — pytest on `tests/functional/`

CI badge at the top of this README reflects the current state of the `main` branch.

## Architecture

```
POST /events ──→ FastAPI ──→ Bloom dedup ──→ Blacklist check
                                    │               │
                                    ▼               ▼
                             CMS increment    Skip Space-Saving
                                    │               │
                                    ▼               ▼
                             Sliding Window   Space-Saving
                              (60 buckets)     (m=1000)
                                    │               │
                                    └───────┬───────┘
                                            ▼
                                     Redis Sorted Set
                                      (30s refresh)
                                            │
                                            ▼
                              GET /trending ← ZREVRANGE
                              GET /count    ← CMS direct read
```

- **Count-Min Sketch** (ε=0.01, δ=0.001, 7×272 cells): Approximate frequency counter over 60 one-minute ring-buffer buckets. Guarantees `estimate ≥ true_count` with probability ≥ 0.999.
- **Space-Saving** (m=1000): Tracks the most frequent item candidates. Employs LRU tie-breaking for eviction.
- **Bloom Filter** (n=10M, p=0.001, ~17 MB): Per-user deduplication keyed on `(user_id, item_id, minute_bucket)`. Prevents a single user from inflating an item's count within the same minute.
- **Redis**: Sorted-set read cache refreshed every 30 seconds. Three pre-computed keys for k ∈ {10, 100, 1000}. Items are excluded from trending if the cache is stale (503) or missing entirely.
- **PostgreSQL**: Persistent store for events (idempotency via `event_id` UUID) and blacklist entries.

## Project Layout

```
├── src/topk/
│   ├── main.py                  # FastAPI app factory + lifespan
│   ├── config.py                # pydantic-settings
│   ├── db.py                    # Async SQLAlchemy engine
│   ├── redis.py                 # Async Redis client
│   ├── routers/
│   │   ├── events.py            # POST /events
│   │   ├── trending.py          # GET /trending, GET /count
│   │   ├── admin.py             # /admin/blacklist CRUD, /admin/reset
│   │   └── health.py            # GET /healthz
│   ├── services/
│   │   ├── cms.py               # Count-Min Sketch
│   │   ├── space_saving.py      # Space-Saving top-K tracker
│   │   ├── bloom.py             # Bloom filter dedup
│   │   ├── window.py            # 60-bucket sliding window
│   │   └── trending.py          # TrendingService orchestration
│   └── models/
│       ├── event.py             # SQLAlchemy Event + Blacklist models
│       └── schemas.py           # Pydantic request/response schemas
├── tests/
│   ├── unit/                    # Pure algorithm tests (CMS, SS, Bloom)
│   └── functional/              # In-process pipeline tests
├── verify/acceptance/           # Black-box per-FR acceptance tests
├── alembic/                     # Database migrations
├── docker-compose.yml           # Compose stack (app + db + redis)
├── Dockerfile                   # Multi-stage Python 3.12-slim build
├── pyproject.toml               # Dependencies + tool config
└── DEPLOY.md                    # Full deployment guide
```

## Limitations

This is an MVP targeting the 1-hour sliding window use case. The following are out of scope:

- **Multi-granularity windows** (1-minute, 24-hour, 7-day) — only 1-hour window is implemented.
- **Trend detection / velocity scoring** — scoring current count against a historical baseline is deferred.
- **Kafka / Flink / Spark** — all stream processing is synchronous and in-process (no distributed aggregation).
- **High-throughput scaling** — the system is not sharded or partitioned. Throughput is bounded by a single FastAPI process.
- **Rate limiting** — no per-user velocity caps at ingestion.
- **Coordinated campaign detection** — Bloom dedup prevents single-user gaming but not multi-user coordinated campaigns.
- **Late event handling** — no batch reconciliation for events arriving outside the current window.
- **Personalized top-K** — results are global, not per-user.
