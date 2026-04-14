# Redis (In-Memory Data Store)

## What It Is

Redis is a free, open-source in-memory database. It stores key-value pairs in RAM, which makes reads extremely fast (sub-millisecond). It supports data structures beyond simple strings: lists, sets, sorted sets, hashes, and streams.

In ML systems, Redis is most commonly used as a low-latency feature serving layer: pre-computed features are loaded into Redis so that when a model needs to score a company, it can fetch all the features in <1ms instead of querying a database or recomputing from raw data.

## Why You Might Want It

The pipeline currently has no real-time scoring path. You run the CLI, it processes everything in batch, and you read the results. Redis would matter if you build:

1. **Event-triggered scoring.** A new data point arrives (e.g., a leadership change at Company X is detected from a news feed). You want to immediately rescore that company's sell probability. The model needs Company X's current features -- employee growth, revenue trajectory, sector activity -- and fetching them from a Postgres query or recomputing them takes 50-200ms. Redis serves them in <1ms.

2. **A web dashboard or API.** If you build a UI where deal team members can look up any company and see its current scores, feature values, and valuation, Redis makes these lookups instant. Without it, each page load triggers database queries and computations.

3. **Feast online store.** If you add Feast (see [feast-feature-store.md](feast-feature-store.md)), its online serving layer can use Redis as a backend for the fastest possible feature retrieval.

## When to Add It

Add Redis when:
- You build a real-time scoring API or webhook endpoint
- Page load times on a dashboard are too slow due to feature lookups
- You adopt Feast and need a fast online store backend

You do NOT need it when:
- The pipeline runs as a batch job (current state)
- You have no real-time scoring requirement
- Your dataset fits in memory (a Python dict is faster than a network call to Redis)

**Simpler alternatives:**
- For caching: a Python `dict` or `functools.lru_cache` if everything runs in one process
- For persistence: SQLite (on-disk, zero configuration, reads in 1-5ms)
- For Feast: SQLite is the default Feast online store and works fine for <100K entities

## How to Add It to This Pipeline

### 1. Install

```bash
pip install redis>=5.0
```

### 2. Run Redis

Locally via Docker (one command):

```bash
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

Or add back to docker-compose:

```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

### 3. Populate Features

After computing features in the pipeline, write them to Redis:

```python
import redis
import json

r = redis.Redis(host="localhost", port=6379, db=0)

def cache_company_features(entity_id: str, features: dict) -> None:
    r.set(f"features:{entity_id}", json.dumps(features), ex=86400)  # expires in 24h

def get_company_features(entity_id: str) -> dict | None:
    data = r.get(f"features:{entity_id}")
    return json.loads(data) if data else None
```

### 4. Wire Into the Pipeline

Add a caching layer after feature computation in the ingestion stage:

```python
# After computing features for a company
features = compute_features(company)
cache_company_features(company.entity_id, features)

# In scoring/valuation, check cache first
cached = get_company_features(entity_id)
if cached:
    feature_vector = np.array([cached[f] for f in FEATURE_NAMES])
else:
    feature_vector = compute_features_from_raw(entity_id)  # fallback
```

### 5. Config

```python
# src/common/config.py
class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_")
    host: str = "localhost"
    port: int = 6379
    db: int = 0
```

```env
# .env
REDIS_HOST=localhost
REDIS_PORT=6379
```

## Cost

Free. Redis is BSD licensed. Redis Cloud (managed hosting) starts at ~$5/month for small instances, but running locally costs nothing. Memory usage depends on your data: 100K companies × 20 features × 8 bytes each ≈ 16MB, which is trivial.
