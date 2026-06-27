# Top-K Trending API — Design

## Overview

The Top-K Trending API tracks the most frequently occurring items in a real-time event stream over a 1-hour sliding window. It accepts batched event ingestion, maintains approximate frequency counts using probabilistic data structures, and serves ranked top-K results with sub-100ms latency.

The broader target (beyond this MVP) scales to 500M DAU and 5M events/s at peak across multiple time windows (1-minute through 7-day) using Kafka, Flink, and Spark. This MVP implements the 1-hour window in-process with pure Python — the foundational algorithms and API contract are the same; distribution and scale are deferred.

## Architecture

```
                     ┌──────────────────────────────────────────────┐
                     │              FastAPI Process                 │
                     │                                              │
  POST /events ─────►│  ┌─────────┐   ┌──────────┐   ┌──────────┐ │
                     │  │  Bloom  │──►│ Blacklist │──►│   CMS    │ │
                     │  │  Filter │   │  Check   │   │ Increment│ │
                     │  └─────────┘   └────┬─────┘   └────┬─────┘ │
                     │                    │               │        │
                     │               skip SS         ┌────▼─────┐ │
                     │                               │  Space-  │ │
                     │                               │  Saving  │ │
                     │                               └────┬─────┘ │
                     │                                    │        │
                     │       ┌────────────────────────────┘        │
                     │       │   Sliding Window                   │
                     │       │   (60 x CMS buckets)               │
                     │       └────────────┬───────────────────────┘
                     │                    │
                     │       ┌────────────▼───────────────────────┐
                     │       │  Trending Service (30s refresh)    │
                     │       └────────────┬───────────────────────┘
                     └────────────────────┼──────────────────────────
                                          │
                     ┌────────────────────▼──────────────────────────
                     │              Redis Sorted Sets                │
                     │    topk:1h:10  |  topk:1h:100  |  topk:1h:1000
                     └────────────────────┬──────────────────────────
                                          │
                     ┌────────────────────▼──────────────────────────
                     │           PostgreSQL (persistence)            │
                     │      events table  |  blacklist table         │
                     └───────────────────────────────────────────────

  GET /trending ──► ZREVRANGE on Redis sorted set (30s cache)
  GET /count    ──► Direct CMS read (no Redis)
```

The write path is synchronous and in-process: event batches arrive at `POST /events`, pass through Bloom filter deduplication and blacklist filtering, then increment both the Count-Min Sketch sliding window and the Space-Saving tracker. Every 30 seconds the Trending Service queries the CMS for each Space-Saving candidate's current count and rebuilds three Redis sorted sets.

The read path splits: `GET /trending` reads from Redis (fast, cached), while `GET /count` reads the CMS directly (no Redis dependency).

## Key Decisions

### Why CMS + Space-Saving over exact counting

Exact counting over a 1-hour window requires storing every distinct item in memory. With millions of distinct items, this grows unboundedly. Count-Min Sketch and Space-Saving are sub-linear space data structures that bound memory to a fixed allocation regardless of how many distinct items appear.

- **Count-Min Sketch** (epsilon=0.01, delta=0.001): 7 rows x 272 columns = 7,616 bytes per bucket. With 60 one-minute buckets, the sliding window consumes ~457 KB total. Guaranteed `estimate >= true_count` with probability 0.999, and overcount by at most `epsilon * N` — tolerable for ranking because relative error shrinks as true frequency grows.
- **Space-Saving** (m=1000): Tracks the most frequent item candidates. Guarantee: any item with frequency > `N/m` is monitored. With 1,000 capacity, items with >0.1% of total events are guaranteed to be tracked. ~130 KB memory.

Together, the state occupies under 1 MB — over 10,000x smaller than exact counting for a window with 1M distinct items.

### Why 60-bucket sliding window

A sliding window over 60 minutes is implemented as a ring buffer of 60 independent CMS instances, each covering one minute. On each minute boundary, the oldest bucket is zeroed and becomes the new current bucket.

This design avoids the memory overhead of tracking individual event timestamps or building complex data structures for arbitrary time ranges. Each query sums all 60 buckets — a mergeable operation with bounded cost (60 x 7 hash lookups = 420 ops per estimate). CMS error is linear in total count, not bucket count, so summing 60 buckets does not compound the error.

### Why Bloom filter dedup per (user, item, minute)

Without deduplication, a single user could inflate an item's count by sending repeated events. A Bloom filter keyed on `(user_id, item_id, minute_bucket)` blocks repeat counts for the same entity within the same minute.

The Bloom filter (capacity=10M, error rate=0.001, ~17 MB) is zeroed on minute boundaries, limiting the false-positive window to one minute. Events without a `user_id` bypass dedup (anonymous traffic is counted as-is).

### Why Redis as a read cache, not a source of truth

Redis stores pre-computed sorted sets refreshed every 30 seconds. It is a cache — all truth lives in the in-memory CMS + Space-Saving state and PostgreSQL. If Redis is unavailable, the health endpoint returns `"redis": "disconnected"` (status 200 if PostgreSQL is up), and `GET /trending` returns 503 with `stale_data: true`. The system degrades gracefully rather than blocking.

## Data Model

```
Event {
  event_id:    uuid (PK)              ← idempotency key
  item_id:     text (not null, indexed)
  user_id:     text (nullable)
  event_type:  text (not null)        ← 'view' | 'click' | 'mention'
  timestamp:   bigint (not null)      ← epoch_ms
  created_at:  timestamptz
}

Blacklist {
  item_id:     text (PK)
  created_at:  timestamptz
}
```

**events** is the persistent store for all ingested events. The `event_id` UUID serves as the idempotency key — duplicate event IDs are detected via PostgreSQL lookup and not double-counted. The `item_id` index supports fast lookups for reconciliation queries.

**blacklist** is the persistent source of truth for blocked items. On startup, the Trending Service loads all entries into an in-memory set. Runtime mutations are dual-written to PostgreSQL and the in-memory set.

All in-memory state (CMS ring buffer, Space-Saving tracker, Bloom filter, blacklist set) is rebuilt from scratch on restart by ingesting the last hour of events from PostgreSQL.

## API Design

Rationale for the endpoint set:

| Endpoint | Design rationale |
|----------|-----------------|
| `POST /events` | Batch ingestion (up to 100 events). Batch amortizes HTTP overhead while keeping payloads small enough for synchronous processing. Returns aggregate counts (accepted, duplicates, blocked) so clients can monitor pipeline health without extra endpoints. |
| `GET /trending?window=1h&k=100` | Top-K retrieval from Redis sorted set. `window` and `k` are validated server-side. Only `1h` window is supported in MVP; the parameter exists for forward compatibility. k in [1, 1000]. |
| `GET /count?item_id=X&window=1h` | Point query reading CMS directly — no Redis dependency. Returns `is_approximate: true` to signal that the count is a CMS estimate, not an exact value. |
| `GET /healthz` | Service health including DB and Redis connectivity. Returns 200 if PostgreSQL is connected (even if Redis is down), 503 only if PostgreSQL is unreachable. |
| `POST /admin/blacklist` | Operator adds items to the blacklist. Dual-writes to PostgreSQL + in-memory set. Idempotent on re-adds. |
| `GET /admin/blacklist` | Lists all blacklisted items from the in-memory set. |
| `DELETE /admin/blacklist` | Removes items from the blacklist. Items re-enter Space-Saving tracking on subsequent events. Idempotent on missing items. |
| `POST /admin/reset` | Resets all in-memory state and clears Redis cache — used for test isolation. Not intended for production use. |

Error conventions: validation errors return 422 with Pydantic detail. Database unavailability returns 503. Cache staleness returns 503 with `stale_data: true`. All responses are JSON.

## Functional Requirements to Acceptance Tests

Each functional requirement maps to one black-box test file in `verify/acceptance/`. These tests talk to the running system over HTTP and do not import application code.

| FR | Test file | Key assertion |
|----|-----------|---------------|
| FR-1: Event ingestion | `test_fr1_event_ingestion.py` | Valid batch to 202 with accepted count; empty batch to 422; invalid type to 422; duplicate event_id to no double-count; batch > 100 to 422 |
| FR-2: CMS counting | `test_fr2_cms_counting.py` | Count >= true count for known items; count = 0 for unknown items; count grows monotonically with more events |
| FR-3: Space-Saving top-K | `test_fr3_space_saving_topk.py` | Skewed data: top item ranks #1 with count >= true frequency; result length <= k; response includes all required fields |
| FR-4: Top-K retrieval | `test_fr4_topk_retrieval.py` | Valid k to 200 with items; k=0, k=-1, k=1001 to 422; invalid/missing window to 422 |
| FR-5: Point query | `test_fr5_point_query.py` | Known item to count > 0; unknown item to count = 0; missing item_id to 422; invalid window to 422; `is_approximate: true` |
| FR-6: Dedup | `test_fr6_dedup.py` | Same (user, item, minute) to single count increment; different users to both counted; no user_id to no dedup |
| FR-7: Health | `test_fr7_health.py` | 200 with status=healthy; postgres and redis connected; no auth required |
| FR-8: Blacklist | `test_fr8_blacklist.py` | Blacklisted items excluded from trending; still counted in CMS; remove to re-enters trending; idempotent add/delete |

The acceptance suite is the contract: `POST /admin/reset` is called before each test to isolate state.

## Test Results

### CI Pipeline

Three GitHub Actions workflows run on every push, PR, and daily schedule:

- **Lint** ([workflow](https://github.com/iliazlobin/sd-top-k-backend-mvp/actions/workflows/lint.yml)): ruff check + format verification. ![Lint](https://github.com/iliazlobin/sd-top-k-backend-mvp/actions/workflows/lint.yml/badge.svg)
- **CI** ([workflow](https://github.com/iliazlobin/sd-top-k-backend-mvp/actions/workflows/ci.yml)): unit tests against PostgreSQL + Redis service containers, plus full Docker Compose e2e acceptance run. ![CI](https://github.com/iliazlobin/sd-top-k-backend-mvp/actions/workflows/ci.yml/badge.svg)
- **Functional** ([workflow](https://github.com/iliazlobin/sd-top-k-backend-mvp/actions/workflows/functional.yml)): pytest on `tests/functional/` — in-process pipeline tests with mocked Redis. ![Functional](https://github.com/iliazlobin/sd-top-k-backend-mvp/actions/workflows/functional.yml/badge.svg)

CI re-runs on every push and daily. Badges at the top of the README reflect the current `main` branch state.

### Test Layers

| Layer | Location | Count | Scope |
|-------|----------|-------|-------|
| Unit | `tests/unit/` | 3 files, ~35 tests | Pure algorithm tests: CMS (creation, increment, estimate, merge, reset, total count), Space-Saving (creation, increment, top-K, capacity, eviction, LRU tie-breaking, monitored items), Bloom filter (creation, add/contains, false-positive rate, reset, composite keys) |
| Functional | `tests/functional/` | 2 files, ~25 tests | In-process pipeline: TrendingService event processing (Bloom dedup, blacklist, CMS counting, Space-Saving integration), Pydantic validation, Redis cache operations, in-memory blacklist CRUD |
| Acceptance | `verify/acceptance/` | 8 files, ~35 tests | Black-box HTTP: one file per functional requirement, using `httpx` against a running instance with state isolation via `POST /admin/reset` |
| App skeleton | `tests/test_app.py` | 1 file, ~5 tests | App factory creation, OpenAPI schema generation, health endpoint structure, router validation (422 on invalid input) |

All three test layers are runnable in CI. The acceptance suite additionally runs in the host e2e loop every 30 minutes against the live compose stack.

## Out of Scope (Full Design Additions)

The full system design targets a high-throughput, multi-window trending platform. This MVP defers:

- **Kafka / Flink / Spark**: Event ingestion and stream processing are in-process Python. The full design partitions events across 64 Kafka partitions with 64 Flink workers.
- **Multi-granularity windows**: Only 1-hour is implemented. Full design adds 1-minute (Space-Saving direct), 24-hour (288x5min CMS buckets), and 7-day (Spark batch + Postgres).
- **Trend detection / velocity scoring**: Computing `current_count / EWMA_baseline` and flagging items with score > 3.0 is deferred.
- **Distributed aggregation**: CMS merge across shards is not implemented. Single-process only.
- **Rate limiting**: Per-user velocity capping (token bucket, 10 events/s) is not implemented.
- **Coordinated campaign detection**: Offline ML batch analysis for multi-user coordinated gaming is deferred.
- **Batch reconciliation for late events**: Events arriving after their window has expired are not re-processed.
- **Personalized top-K**: Results are global, not per-user.
