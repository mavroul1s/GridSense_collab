# GridSense — Polyglot Persistence IoT Platform for Smart Grid Monitoring

A FastAPI application backed by five databases — Apache Cassandra, Neo4j, MongoDB, PostgreSQL, and Redis — orchestrated with Docker Compose. Built for the Advanced Data Management course final assignment.

## Architecture

| Database | Role | Why |
|---|---|---|
| **Cassandra** | Time-series sensor readings | High write throughput (40k+ events/sec), partition-key design for O(1) per-sensor reads |
| **Neo4j** | Network topology & fault propagation | Native graph traversal for multi-hop downstream impact queries |
| **MongoDB** | Equipment catalog | Schema flexibility across heterogeneous asset types (Transformer/SmartMeter/ProtectionRelay) |
| **PostgreSQL** | Billing & consumer accounts | ACID transactions required for financial correctness, JSONB for flexible tariff structures |
| **Redis** | Caching & pub/sub alerts | Sub-millisecond cache-aside reads, TTL-based transformer status, live alert distribution |

Full reasoning for each technology choice is documented in `PART_A_ANSWERS.md`.

## Project Structure
gridsense/

├── docker-compose.yml       # 8 services: 5 DBs + 2 init containers + API

├── cql/init.cql              # Cassandra schema (executed by cassandra-init)

├── neo4j/import/seed.cypher  # Neo4j seed data (executed by neo4j-init)

├── pg/init.sql                # PostgreSQL schema (auto-run by postgres entrypoint)

├── scripts/seed.py            # Populates all 5 databases with test data

└── api/

├── Dockerfile

├── main.py                # FastAPI app + lifespan DB connection management

├── requirements.txt

├── db/                    # One connection module per database

├── models/                # Pydantic request/response models

└── routers/                # 5 routers, 14 endpoints total

## Running the Stack

Prerequisites: Docker Desktop, a `.env` file at the repo root (see `.env.example`).

```bash
docker compose up --build
```

Wait ~90 seconds for Cassandra to reach healthy status before the API becomes available. Verify with:

```bash
curl http://localhost:8000/health
```

Interactive API documentation: **http://localhost:8000/docs**

## Seeding Test Data

```bash
docker cp scripts/seed.py gridsense_api:/app/scripts/seed.py
docker exec -it gridsense_api python scripts/seed.py
```

This populates:
- **Cassandra**: 50,000 readings × 4 metric types across 20 sensors (200,000 total writes across 2 tables)
- **MongoDB**: 30 equipment records across 3 schema shapes (Transformer, SmartMeter, ProtectionRelay)
- **PostgreSQL**: 100 consumer accounts + 100 invoices
- **Neo4j**: 2 GridSupplyPoints, 10 Substations, 40 Transformers, 200 SmartMeters (seeded automatically at startup via `neo4j-init`)
- **Redis**: 5 test alerts pushed to the rolling buffer

The seed script is idempotent — re-running it does not create duplicates.

## API Endpoints

14 endpoints across 5 routers:

| Method | Path | Backend |
|---|---|---|
| POST | `/sensors/readings` | Cassandra |
| GET | `/sensors/{sensor_id}/readings` | Cassandra |
| GET | `/sensors/{sensor_id}/summary` | Redis cache → Cassandra |
| GET | `/grid/fault-impact/{node_id}` | Neo4j |
| GET | `/grid/restore-paths/{node_id}` | Neo4j |
| POST | `/grid/nodes` | Neo4j |
| POST | `/grid/relationships` | Neo4j |
| GET | `/equipment/{asset_id}` | MongoDB |
| POST | `/equipment` | MongoDB |
| PATCH | `/equipment/{asset_id}` | MongoDB |
| GET | `/billing/account/{premise_id}` | PostgreSQL |
| POST | `/billing/invoice` | PostgreSQL |
| GET | `/alerts/active` | Redis |
| POST | `/alerts/publish` | Cassandra + Redis |

## Example API Calls

### 1. Get sensor readings (Cassandra — partition key read)

```bash
curl http://localhost:8000/sensors/SENSOR_001/readings
```

**Response:**
```json
[
  {
    "sensor_id": "SENSOR_001",
    "reading_time": "2026-03-22T01:37:29.431000",
    "metric_type": "current",
    "value": 4.34,
    "unit": "A",
    "quality_flag": 0
  },
  {
    "sensor_id": "SENSOR_001",
    "reading_time": "2026-03-22T01:37:29.431000",
    "metric_type": "voltage",
    "value": 227.8,
    "unit": "V",
    "quality_flag": 0
  }
]
```

This query uses only the partition key (`sensor_id`) — a single-partition read, no cluster-wide scan. `CLUSTERING ORDER BY reading_time DESC` means the most recent readings are returned first without an explicit sort.

### 2. Fault impact traversal (Neo4j — bounded variable-length path)

```bash
curl "http://localhost:8000/grid/fault-impact/SS_001?max_depth=3"
```

**Response (truncated):**
```json
{
  "origin_node_id": "SS_001",
  "origin_node_type": "Substation",
  "max_depth_used": 3,
  "affected_count": 24,
  "affected_nodes": [
    {
      "node_type": "Transformer",
      "node_id": "TX_1",
      "name": "TX_1",
      "depth": 1,
      "rating_kVA": 250
    },
    {
      "node_type": "SmartMeter",
      "node_id": "SM_1",
      "name": null,
      "depth": 2,
      "premise_id": "PREM_1",
      "tariff_class": "residential"
    }
  ]
}
```

`max_depth` is validated as a bounded integer (1–10) before being interpolated into the Cypher traversal pattern. Depth is computed from `length(path)` on the named `MATCH` path, not `shortestPath()` in the `RETURN` clause — avoiding a full-graph scan per row.

### 3. Billing account lookup (PostgreSQL — JSONB tariff structure)

```bash
curl http://localhost:8000/billing/account/PREM_1
```

**Response:**
```json
{
  "premise_id": "PREM_1",
  "name": "Nikos Georgiou",
  "address": "31 Venizelou St, Thessaloniki, Unit 1",
  "tariff_info": {
    "tariff_class": "residential",
    "rate_per_kwh": 0.1666,
    "standing_charge": 0.3467
  },
  "balance": 87.42,
  "created_at": "2026-06-18T08:09:00.123Z",
  "updated_at": "2026-06-18T08:09:00.123Z"
}
```

`tariff_info` is stored as `JSONB` with a GIN index, enabling `@>` containment queries (e.g., filtering all residential accounts) without a full table scan. Monetary values use `NUMERIC(12,2)`, never `FLOAT`, to avoid binary rounding errors in billing calculations.

## Known Design Constraints

A full list of intentional traps identified in the assignment's starter materials — and the corrected approach taken instead — is documented in `GRIDSENSE_TRAP_ANALYSIS.md`. Summary of the most significant:

- Cassandra and Neo4j do not support `docker-entrypoint-initdb.d`-style auto-initialization; dedicated `cassandra-init` and `neo4j-init` containers execute schema/seed files via `cqlsh` and `cypher-shell` respectively after a healthcheck confirms the database is ready.
- Cypher does not allow query parameters as variable-length path bounds (`*1..$depth` is invalid); the bound is validated server-side (1–10) and interpolated as an f-string.
- The `sensor_readings` clustering key includes `metric_type` to prevent Last-Write-Wins data loss when multiple metrics arrive at the same timestamp.
- All inter-container connections use Docker Compose service names (`timeseries-db`, `graph-db`, `catalog-db`, `billing-db`, `cache`), never `localhost`.

## AI Use Disclosure

See `AI_DISCLOSURE.md` for the full disclosure of AI assistance used in this project, per Section 1.5 of the assessment specification.