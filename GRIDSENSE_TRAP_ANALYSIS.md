# GridSense Assignment — Trap Analysis & Slide-Based Implementation Guide

> **Purpose:** This document maps every trap in the assignment PDF against what the slides actually teach. Follow the "CORRECT APPROACH" sections exclusively. Do NOT copy the assignment's code examples directly.

---

## 1. OVERVIEW OF TRAPS

The assignment contains **11 confirmed traps** across the docker-compose, CQL schema, Cypher code, and API structure. They fall into three categories:

| Category | Trap Count | Risk Level |
|---|---|---|
| Docker / infra config bugs | 3 | Critical — system won't start |
| Cypher / CQL schema bugs | 4 | Critical — queries will fail |
| Incomplete / undefined code | 4 | High — runtime errors |

---

## 2. TRAP-BY-TRAP BREAKDOWN

---

### TRAP 1 — Cassandra `docker-entrypoint-initdb.d` ❌ CRITICAL

**Where:** B.2 / B.3 in the assignment — the starter `docker-compose.yml`

**What the assignment shows:**
```yaml
volumes:
  - cassandra_data:/var/lib/cassandra
  - ./cql/init.cql:/docker-entrypoint-initdb.d/init.cql   # ← TRAP
```

**Why it's a trap:** `docker-entrypoint-initdb.d` is a **PostgreSQL and MySQL** convention. The official `cassandra:4.1` Docker image does **not** watch that directory. Mounting the file there does nothing — Cassandra will start with no keyspace, no tables, and your API will crash with `NoHostAvailable` or table-not-found errors.

**What the slides say (Week 6, Steps 2–3):**
```bash
docker run -d --name cassandra-lab -p 9042:9042 cassandra:4.1
# Wait ~30 seconds, then:
docker exec -it cassandra-lab cqlsh
# Then run your CQL manually inside cqlsh
```

**CORRECT APPROACH:** Use a dedicated init container or an entrypoint wrapper script:
```yaml
# In docker-compose.yml
timeseries-db:
  image: cassandra:4.1
  container_name: gridsense_cassandra
  ...
  volumes:
    - cassandra_data:/var/lib/cassandra

cassandra-init:
  image: cassandra:4.1
  depends_on:
    timeseries-db:
      condition: service_healthy
  volumes:
    - ./cql/init.cql:/init.cql
  command: >
    bash -c "sleep 5 && cqlsh timeseries-db -f /init.cql"
  restart: on-failure
```

Or in your `scripts/seed.py`, connect and execute the CQL programmatically after the container is healthy.

---

### TRAP 2 — Neo4j 5 Wrong Memory Environment Variable ❌ CRITICAL

**Where:** B.2 starter `docker-compose.yml`

**What the assignment shows:**
```yaml
environment:
  NEO4J_dbms_memory_heap_max__size: "512M"   # ← TRAP
```

**Why it's a trap:** Neo4j **5.x** renamed all `dbms.*` configuration keys to `server.*`. The variable `NEO4J_dbms_memory_heap_max__size` maps to the old `dbms.memory.heap.max_size` which was deprecated/removed in Neo4j 5. It will be **silently ignored** — Neo4j starts but with default memory settings, which can cause OOM crashes.

**CORRECT APPROACH for Neo4j 5:**
```yaml
environment:
  NEO4J_AUTH: "neo4j/${NEO4J_PASSWORD}"
  NEO4J_server_memory_heap_max__size: "512M"    # ← correct for Neo4j 5
  NEO4J_server_memory_heap_initial__size: "256M"
```

---

### TRAP 3 — Neo4j Seed File Is NOT Auto-Executed ❌ CRITICAL

**Where:** B.4 — the volume mount for seed.cypher

**What the assignment implies:** By mounting `./neo4j/import/seed.cypher` to `/import` inside the Neo4j container, it implies the file will run at startup.

```yaml
volumes:
  - ./neo4j/import:/import   # ← does NOT auto-execute anything
```

**Why it's a trap:** Neo4j does **not** auto-execute Cypher files on startup. The `/import` directory is only used for `LOAD CSV` and similar commands that reference files by name. Neo4j will start completely empty with no constraints or seed data.

**CORRECT APPROACH:** Add an init service that uses `cypher-shell`:
```yaml
neo4j-init:
  image: neo4j:5-community
  depends_on:
    graph-db:
      condition: service_healthy
  volumes:
    - ./neo4j/import:/import
  command: >
    bash -c "cypher-shell -a bolt://graph-db:7687 -u neo4j -p ${NEO4J_PASSWORD} -f /import/seed.cypher"
  restart: on-failure
```

---

### TRAP 4 — Cypher: Parameter Cannot Be Used as Path Length Bound ❌ CRITICAL

**Where:** B.6 — Sample endpoint `grid.py`

**What the assignment shows:**
```python
cypher = """
MATCH (origin {node_id: $node_id})
MATCH (origin)-[:FEEDS|SUPPLIES|CONNECTS_TO*1..$depth]->(downstream)
...
"""
result = await session.run(cypher, node_id=node_id, depth=max_depth)
```

**Why it's a trap:** In Cypher, **you cannot use a query parameter (`$depth`) as the upper bound of a variable-length path**. The syntax `*1..$depth` is **invalid**. Neo4j will throw a syntax error at runtime.

**CORRECT APPROACH:** Cap depth server-side with a fixed maximum, or build the query string with string formatting (with validated, bounded input):
```python
# max_depth is already validated (≤ 10) before this point
cypher = f"""
MATCH (origin {{node_id: $node_id}})
MATCH (origin)-[:FEEDS|SUPPLIES|CONNECTS_TO*1..{max_depth}]->(downstream)
RETURN labels(downstream)[0] AS node_type,
       downstream.node_id AS node_id,
       downstream.name AS name
ORDER BY node_id
"""
result = await session.run(cypher, node_id=node_id)
```

Note: use f-string only for the bounded integer `max_depth` — never for user-supplied string data.

---

### TRAP 5 — `shortestPath()` Bug in Sample Cypher ❌ HIGH

**Where:** B.6 — Sample endpoint `grid.py`

**What the assignment shows:**
```cypher
RETURN labels(downstream)[0] AS node_type,
       downstream.node_id AS node_id,
       downstream.name AS name,
       length(
         shortestPath((origin)-[:FEEDS|SUPPLIES|CONNECTS_TO*]-(downstream))
       ) AS depth
```

**Why it's a trap:** Two problems:
1. `shortestPath` in a `RETURN` clause with an unbounded `*` pattern `(origin)-[...]-(downstream)` can traverse the **entire graph** for every row — catastrophic performance.
2. You already have the path from the variable-length `MATCH` above. Computing depth again with `shortestPath` is redundant and expensive.

**CORRECT APPROACH:** Track depth using `apoc.path` if APOC is available, or accept that depth information comes from the traversal itself. A simple approach:
```cypher
MATCH (origin {node_id: $node_id})
MATCH path = (origin)-[:FEEDS|SUPPLIES|CONNECTS_TO*1..6]->(downstream)
RETURN labels(downstream)[0] AS node_type,
       downstream.node_id AS node_id,
       coalesce(downstream.name, downstream.asset_id, downstream.meter_id) AS name,
       length(path) AS depth
ORDER BY depth
```

---

### TRAP 6 — `_node_exists` Function Referenced But Never Defined ❌ HIGH

**Where:** B.6 — Sample endpoint `grid.py`

**What the assignment shows:**
```python
if not records and not await _node_exists(driver, node_id):
    raise HTTPException(...)
```

**Why it's a trap:** `_node_exists` is never defined anywhere in the assignment. Copying this code will produce a `NameError` at runtime.

**CORRECT APPROACH:** Implement the helper yourself:
```python
async def _node_exists(driver, node_id: str) -> bool:
    async with driver.session(database="neo4j") as session:
        result = await session.run(
            "MATCH (n {node_id: $node_id}) RETURN count(n) AS cnt",
            node_id=node_id
        )
        record = await result.single()
        return record["cnt"] > 0
```

---

### TRAP 7 — `TINYINT` Is Not in the Course Data Types ⚠️ MEDIUM

**Where:** B.3 — CQL schema for `sensor_readings`

**What the assignment shows:**
```sql
quality_flag TINYINT,  -- 0=good, 1=suspect, 2=bad
```

**Why it's a trap:** Week 6 slides (Slide 34) define exactly these CQL types: `TEXT/VARCHAR, UUID/TIMEUUID, TIMESTAMP, INT/BIGINT/FLOAT/DOUBLE, BOOLEAN, LIST<T>, SET<T>, MAP<K,V>, COUNTER`. **TINYINT is not listed**. The professor only wants you to use the types taught in class.

**CORRECT APPROACH per slides:**
```sql
quality_flag INT,  -- 0=good, 1=suspect, 2=bad
```

---

### TRAP 8 — Seed Script Not Idempotent (MERGE vs CREATE) ⚠️ HIGH

**Where:** B.4 — seed.cypher relationship creation

**What the assignment shows:**
```cypher
-- Comment says "MATCH before MERGE to avoid duplicates"
MATCH (g:GridSupplyPoint {gsp_id:"GSP_NORTH"})
MATCH (s:Substation {substation_id:"SS_001"})
CREATE (g)-[:FEEDS {feeder_id:"F_001", voltage_kV:11, length_km:2.4}]->(s);
```

**Why it's a trap:** The comment says "MATCH before MERGE to avoid duplicates" but then uses `CREATE` for the relationship — not `MERGE`. Running the seed script twice will create **duplicate relationships**. Assignment B.8 explicitly requires: *"running it twice must not create duplicate records."*

**CORRECT APPROACH — use MERGE for relationships too:**
```cypher
MATCH (g:GridSupplyPoint {gsp_id:"GSP_NORTH"})
MATCH (s:Substation {substation_id:"SS_001"})
MERGE (g)-[r:FEEDS {feeder_id:"F_001"}]->(s)
ON CREATE SET r.voltage_kV = 11, r.length_km = 2.4;
```

Or use `MERGE` with an alias:
```cypher
MATCH (g:GridSupplyPoint {gsp_id:"GSP_NORTH"})
MATCH (s:Substation {substation_id:"SS_001"})
MERGE (g)-[r:FEEDS {feeder_id:"F_001"}]->(s)
SET r.voltage_kV = 11, r.length_km = 2.4;
```

---

### TRAP 9 — `sensor_readings` Schema Loses Data for Multiple Metric Types ⚠️ HIGH

**Where:** B.3 — CQL sensor_readings table

**What the assignment shows:**
```sql
CREATE TABLE IF NOT EXISTS sensor_readings (
  sensor_id    TEXT,
  reading_time TIMESTAMP,
  metric_type  TEXT,   -- 'voltage', 'current', 'power_factor', 'temp'
  value        FLOAT,
  ...
  PRIMARY KEY ((sensor_id), reading_time)
)
```

**Why it's a trap:** The partition key is `(sensor_id, reading_time)`. If the same sensor sends `voltage`, `current`, and `power_factor` all at the **same timestamp**, only the last write survives (Cassandra's Last-Write-Wins). You lose 2 out of 3 readings silently.

**CORRECT APPROACH per Week 6 slides (query-driven design):** Add `metric_type` to the clustering key:
```sql
CREATE TABLE IF NOT EXISTS sensor_readings (
  sensor_id    TEXT,
  reading_time TIMESTAMP,
  metric_type  TEXT,
  value        FLOAT,
  unit         TEXT,
  quality_flag INT,
  PRIMARY KEY ((sensor_id), reading_time, metric_type)
) WITH CLUSTERING ORDER BY (reading_time DESC, metric_type ASC)
AND default_time_to_live = 7776000;
```

---

### TRAP 10 — MongoDB Connection URL Uses `localhost` Inside Docker ⚠️ HIGH

**Where:** Week 5 Lab slides vs. Docker Compose networking (Week 4 slides)

**What the Week 5 lab shows:**
```python
client = AsyncIOMotorClient("mongodb://localhost:27017")
```

**Why it's a trap:** In the lab (Week 5), FastAPI runs on your **host machine** and MongoDB is in Docker with port `27017` exposed — so `localhost` works. In this assignment, **FastAPI also runs inside Docker** (the `api` container). From inside Docker, `localhost` refers to the `api` container itself, not the MongoDB container. The connection will fail.

**CORRECT APPROACH per Week 4 slides (Compose Networking, Slide 16):**
> *"Containers discover each other using their service names as hostnames."*

```python
# In db/mongo.py
client = AsyncIOMotorClient("mongodb://catalog-db:27017")
#                                          ↑ Docker service name
```

Same applies to all other DB connections:
```python
# Cassandra
cluster = Cluster(['timeseries-db'])

# PostgreSQL
pool = await asyncpg.create_pool("postgresql://user:pass@billing-db:5432/db")

# Redis
redis = await aioredis.from_url("redis://cache:6379")

# Neo4j
driver = AsyncGraphDatabase.driver("bolt://graph-db:7687", ...)
```

---

### TRAP 11 — `version: "3.9"` Is Deprecated in Docker Compose V2 ⚠️ LOW

**Where:** B.2 starter `docker-compose.yml`

**What the assignment shows:**
```yaml
version: "3.9"
services:
  ...
```

**Why it's a trap:** Week 4 slides don't include a `version:` key in the compose file examples. Modern Docker Compose V2 (which ships with Docker Desktop and current Ubuntu Docker packages) **ignores** the `version` key and emits a deprecation warning. It still works, but it signals you copied from the assignment instead of following the slides.

**CORRECT APPROACH per Week 4 slides:**
```yaml
# No version key — Docker Compose V2 style
services:
  postgres:
    image: postgres:15
    ...
```

---

## 3. WHAT THE SLIDES SAY TO BUILD (THE AUTHORITATIVE GUIDE)

### 3.1 Docker Compose Structure (Week 4, Slides 13–16)

```yaml
# No version key
services:
  timeseries-db:           # Cassandra — Week 6 lab pattern
  graph-db:                # Neo4j — new in assignment
  catalog-db:              # MongoDB — Week 5 lab pattern
  billing-db:              # PostgreSQL — Week 4 lab pattern
  cache:                   # Redis — Week 4 lab pattern
  api:                     # FastAPI — Week 5 lab pattern
  cassandra-init:          # Helper: runs init.cql via cqlsh (NOT docker-entrypoint-initdb.d)
  neo4j-init:              # Helper: runs seed.cypher via cypher-shell
```

All services share one network. Services use their **service name** as the hostname.

### 3.2 Cassandra Setup (Week 6, Steps 1–5)

- Run container → wait for healthy → execute CQL via `cqlsh` or `docker exec`
- No `docker-entrypoint-initdb.d`
- Data types from Slide 34 ONLY: TEXT, UUID, TIMEUUID, TIMESTAMP, INT, BIGINT, FLOAT, DOUBLE, BOOLEAN, LIST, SET, MAP, COUNTER
- Keyspace with `SimpleStrategy` for dev (RF=1), `NetworkTopologyStrategy` for production (Week 6, Slide 29)
- Query-driven design: one table per query pattern (Week 6, Slide 31)
- Partition key = equality filter; Clustering key = range/sort (Week 6, Slide 14)

### 3.3 FastAPI + MongoDB (Week 5 Lab + Slides)

```python
# db/mongo.py
from motor.motor_asyncio import AsyncIOMotorClient

client = AsyncIOMotorClient("mongodb://catalog-db:27017")  # service name
db = client.gridsense
```

```python
# Endpoint pattern (Week 5, Slide 83)
@router.post("/equipment/")
async def create_equipment(item: EquipmentModel):
    await db.equipment.insert_one(item.model_dump())
    return {"status": "created"}

@router.get("/equipment/{asset_id}")
async def get_equipment(asset_id: str):
    doc = await db.equipment.find_one({"asset_id": asset_id})
    if doc:
        doc["_id"] = str(doc["_id"])  # serialize ObjectId
    return doc
```

### 3.4 FastAPI + Redis (Week 4, Slide 41 + Week 5)

```python
# Cache-aside pattern (Week 4, Slide 24)
async def get_summary(sensor_id: str):
    cached = await redis.get(f"summary:{sensor_id}")
    if cached:
        return json.loads(cached)          # Cache HIT
    data = await query_cassandra(sensor_id)  # Cache MISS → hit DB
    await redis.setex(f"summary:{sensor_id}", 30, json.dumps(data))  # TTL=30s
    return data
```

### 3.5 FastAPI + PostgreSQL JSONB (Week 4, Slides 33–36)

```python
# Use asyncpg for async PostgreSQL
# JSONB column with @> containment operator and GIN index
# Week 4, Slide 36: "Use @> (Containment) to check if JSON contains a subset"
```

```sql
-- Week 4 pattern (Slide 35)
CREATE TABLE IF NOT EXISTS consumer_accounts (
  premise_id   VARCHAR(50) PRIMARY KEY,
  name         VARCHAR(200),
  address      TEXT,
  tariff_info  JSONB,
  balance      NUMERIC(12,2)
);
CREATE INDEX idx_tariff_gin ON consumer_accounts USING GIN (tariff_info);
```

### 3.6 API Structure (Week 5, Slides 59, 83–84)

Follows Week 5 CRUD pattern:
- `POST` → `insert_one` / `INSERT INTO` / Cassandra write
- `GET` → `find_one` / `SELECT` / Cassandra read
- `DELETE` → `delete_one` / `DELETE FROM`
- `PATCH` → `update_one` with `$set`

---

## 4. CORRECT `docker-compose.yml` SKELETON

```yaml
services:

  timeseries-db:
    image: cassandra:4.1
    container_name: gridsense_cassandra
    restart: unless-stopped
    ports:
      - "9042:9042"
    environment:
      CASSANDRA_CLUSTER_NAME: "GridSenseCluster"
      MAX_HEAP_SIZE: "512M"
      HEAP_NEWSIZE: "128M"
    volumes:
      - cassandra_data:/var/lib/cassandra
    healthcheck:
      test: ["CMD-SHELL", "nodetool status | grep -q '^UN'"]
      interval: 30s
      timeout: 10s
      retries: 10

  cassandra-init:
    image: cassandra:4.1
    depends_on:
      timeseries-db:
        condition: service_healthy
    volumes:
      - ./cql/init.cql:/init.cql
    command: bash -c "sleep 5 && cqlsh timeseries-db -f /init.cql"
    restart: on-failure

  graph-db:
    image: neo4j:5-community
    container_name: gridsense_neo4j
    restart: unless-stopped
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: "neo4j/${NEO4J_PASSWORD}"
      NEO4J_PLUGINS: '["apoc"]'
      NEO4J_server_memory_heap_max__size: "512M"    # ← correct for Neo4j 5
    volumes:
      - neo4j_data:/data
      - ./neo4j/import:/import
    healthcheck:
      test: ["CMD", "wget", "-O", "/dev/null", "http://localhost:7474"]
      interval: 20s
      timeout: 5s
      retries: 8

  neo4j-init:
    image: neo4j:5-community
    depends_on:
      graph-db:
        condition: service_healthy
    volumes:
      - ./neo4j/import:/import
    environment:
      NEO4J_PASSWORD: "${NEO4J_PASSWORD}"
    command: >
      bash -c "cypher-shell -a bolt://graph-db:7687 -u neo4j -p ${NEO4J_PASSWORD} -f /import/seed.cypher"
    restart: on-failure

  catalog-db:
    image: mongo:7
    container_name: gridsense_mongo
    restart: unless-stopped
    ports:
      - "27017:27017"
    volumes:
      - mongo_data:/data/db

  billing-db:
    image: postgres:15
    container_name: gridsense_postgres
    restart: unless-stopped
    ports:
      - "5432:5432"
    env_file:
      - .env
    volumes:
      - pg_data:/var/lib/postgresql/data

  cache:
    image: redis:7-alpine
    container_name: gridsense_redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  api:
    build: ./api
    container_name: gridsense_api
    restart: unless-stopped
    ports:
      - "8000:8000"
    env_file:
      - .env
    depends_on:
      timeseries-db:
        condition: service_healthy
      graph-db:
        condition: service_healthy
      catalog-db:
        condition: service_started
      billing-db:
        condition: service_started
      cache:
        condition: service_started

volumes:
  cassandra_data:
  neo4j_data:
  mongo_data:
  pg_data:
  redis_data:

networks:
  default:
    name: gridsense_net
```

---

## 5. CORRECT CQL SCHEMA (Following Week 6 Slides)

```sql
-- cql/init.cql

CREATE KEYSPACE IF NOT EXISTS gridsense
WITH replication = {
  'class': 'SimpleStrategy',
  'replication_factor': 1
};

USE gridsense;

-- Pattern 1: readings per sensor (Week 6, Slide 32)
-- metric_type added to clustering key to prevent LWW data loss
CREATE TABLE IF NOT EXISTS sensor_readings (
  sensor_id    TEXT,
  reading_time TIMESTAMP,
  metric_type  TEXT,
  value        FLOAT,
  unit         TEXT,
  quality_flag INT,
  PRIMARY KEY ((sensor_id), reading_time, metric_type)
) WITH CLUSTERING ORDER BY (reading_time DESC, metric_type ASC)
AND default_time_to_live = 7776000;

-- Pattern 2: cross-network dashboard (query-driven, one table per query — Week 6 Slide 31)
CREATE TABLE IF NOT EXISTS sensor_readings_by_time (
  time_bucket  TEXT,
  reading_time TIMESTAMP,
  sensor_id    TEXT,
  metric_type  TEXT,
  value        FLOAT,
  PRIMARY KEY ((time_bucket), reading_time, sensor_id)
) WITH CLUSTERING ORDER BY (reading_time DESC);

-- Relay event log (append-only, TIMEUUID preserves causal order — Week 6 Slide 34)
CREATE TABLE IF NOT EXISTS relay_events (
  feeder_id    TEXT,
  event_time   TIMEUUID,
  relay_id     TEXT,
  event_type   TEXT,
  fault_type   TEXT,
  current_kA   FLOAT,
  PRIMARY KEY ((feeder_id), event_time)
) WITH CLUSTERING ORDER BY (event_time ASC);
```

---

## 6. QUICK-REFERENCE CHEAT SHEET

| Assignment says | Slide says | Use this |
|---|---|---|
| `docker-entrypoint-initdb.d` for Cassandra | Manual `cqlsh` exec | Separate `cassandra-init` container |
| `NEO4J_dbms_memory_heap_max__size` | N/A (not in slides) | `NEO4J_server_memory_heap_max__size` |
| Mount seed.cypher → auto-runs | Not mentioned | `neo4j-init` container with `cypher-shell` |
| `*1..$depth` in Cypher | N/A | f-string with validated int: `*1..{max_depth}` |
| `shortestPath()` in RETURN | N/A | `length(path)` from named path in MATCH |
| `_node_exists()` helper | N/A | Write it yourself (shown above) |
| `TINYINT` | Not in Week 6 types | `INT` |
| `CREATE` for seed relationships | N/A | `MERGE` for idempotency |
| Single PK `(sensor_id, reading_time)` with metric_type column | One table per query (Slide 31) | Add `metric_type` to clustering key |
| `localhost` for DB connections | Service name as hostname (Week 4 Slide 16) | `catalog-db`, `timeseries-db`, etc. |
| `version: "3.9"` in compose | No version key (Week 4 examples) | Omit the version key |

---

## 7. TECHNOLOGIES AND WHERE EACH COMES FROM IN SLIDES

| Technology | Slide Coverage | Key Pattern to Follow |
|---|---|---|
| Docker + Compose | Week 4, Slides 5–17 | `docker compose up -d`, service names as hostnames |
| Redis | Week 4, Slides 21–25 | Cache-aside pattern, `SETEX` for TTL |
| PostgreSQL + JSONB | Week 4, Slides 32–36 | `JSONB` column, `@>` operator, `GIN` index |
| MongoDB + Motor | Week 5, Slides 77–84 + Week 5 Lab | `AsyncIOMotorClient`, `insert_one`, `find_one` |
| FastAPI + Pydantic | Week 5, Slides 77–84 | `BaseModel`, `model_dump()`, CRUD endpoints |
| Apache Cassandra | Week 6, All slides | `cqlsh` manual init, query-driven schema |
| CQL Query Design | Week 6, Slides 28–33 | Partition key = equality, Clustering = range/sort |
| Neo4j / Cypher | Referenced Week 7 (upcoming) | Not yet in slides — use Neo4j docs carefully |

---

*Last updated: based on Week 3, 4, 5, 5-LAB, and 6 slides + full assignment PDF analysis.*

---

## 8. CORRECTIONS TO THIS DOCUMENT (Self-Audit)

Two bugs existed in the original version of this file — fixed in this version:

| Bug | Original (wrong) | Fixed |
|---|---|---|
| Trap 8 MERGE alias | `g_to_s.voltage_kV` (undefined alias) | `r.voltage_kV` (relationship alias `r`) |
| Cassandra-init hostname | `cqlsh gridsense_cassandra` (container name) | `cqlsh timeseries-db` (service name — more reliable in Compose DNS) |

---

## 9. REQUIRED `.env` FILE TEMPLATE

The assignment deducts **5% automatically** if credentials appear in `docker-compose.yml` or committed code. Create this file and add it to `.gitignore`:

```bash
# .env  — DO NOT COMMIT THIS FILE
# Add to .gitignore: echo ".env" >> .gitignore

# PostgreSQL
POSTGRES_USER=admin
POSTGRES_PASSWORD=changeme_postgres
POSTGRES_DB=gridsense

# Neo4j
NEO4J_PASSWORD=changeme_neo4j

# Redis (no auth required for dev, but good habit)
# REDIS_PASSWORD=changeme_redis

# FastAPI app secrets
SECRET_KEY=changeme_secret
```

All services in `docker-compose.yml` reference these via `env_file: - .env` or `${VAR_NAME}` syntax.

---

## 10. RECOMMENDED BUILD ORDER

Build the project in this sequence to avoid integration pain:

1. **docker-compose.yml** — get all 6 containers starting healthy
2. **cql/init.cql** — verify schema via `docker exec -it gridsense_cassandra cqlsh`
3. **neo4j/import/seed.cypher** — verify via Neo4j Browser at `localhost:7474`
4. **api/db/** — all 5 DB connection modules (Cassandra, Neo4j, Mongo, Postgres, Redis)
5. **api/models/** — all Pydantic models for request/response validation
6. **api/routers/sensors.py** — first endpoint (Cassandra, simplest write/read)
7. **api/routers/equipment.py** — MongoDB CRUD (follows Week 5 lab exactly)
8. **api/routers/billing.py** — PostgreSQL + JSONB
9. **api/routers/alerts.py** — Redis Pub/Sub
10. **api/routers/grid.py** — Neo4j (most complex, do last)
11. **scripts/seed.py** — populate all DBs with test data
12. **README.md** — document the 3 required example API calls

