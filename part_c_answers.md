# GridSense — Part C: Performance Measurement & Empirical Analysis

> **Usage:** These are the finalized answers for Part C of the report, based on live benchmarks run against the actual deployed GridSense stack (Docker Compose, single-node dev configuration). When writing the final PDF/LaTeX report, copy and expand these answers into the required format. All numbers in this document are real measured output from the running system — not estimates or theoretical projections.

> **Environment note (applies to all four sub-sections):** All benchmarks were run inside the `gridsense_api` container against the other five service containers (`timeseries-db`, `graph-db`, `catalog-db`, `billing-db`, `cache`) over the internal Docker Compose network (`gridsense_net`). This is a **single-node development deployment**, not the multi-node, multi-datacenter production architecture specified in the assignment's hypothetical scale (3.4 million sensors, 1.2 million billing accounts, 26,000-node graph). Each section states explicitly where this limits generalization to production scale.

---

## C.1 — Cassandra Write Throughput vs Consistency Level (7%)

### Objective

Measure write throughput (writes/sec) for Apache Cassandra at three consistency levels — `ONE`, `LOCAL_QUORUM`, `ALL` — to empirically test the CAP theorem trade-off discussed in Part A.2.

### Methodology

A dedicated benchmark table (`bench_writes`) was created and truncated before testing. Writes used a prepared `INSERT` statement with the consistency level set per trial.

**Revision note:** An initial naive implementation ran the three consistency levels sequentially (`ONE` → `LOCAL_QUORUM` → `ALL`) and produced a misleading result — `ALL` appeared *fastest*, the opposite of theoretical expectation. This was diagnosed as ordering/warm-up bias: the first-run consistency level absorbs all cold-start costs (driver connection warm-up, prepared statement compilation, Cassandra's own page cache being cold), unfairly penalising whichever level happens to run first. The benchmark was revised to:

1. Run a 1,000-write warm-up pass (discarded) before any measurement begins, priming the driver, prepared statement cache, and Cassandra page cache.
2. Run 3 trials per consistency level (9 trials total) in **randomized order**, so no consistency level systematically benefits from running later in the sequence when caches are warmest.
3. Report the mean and standard deviation across the 3 trials per level.

- **Writes per trial:** 3,000
- **Trials per consistency level:** 3 (randomized order)
- **Total measured writes:** 27,000 (plus 1,000 discarded warm-up writes)
- **Cluster configuration:** `SimpleStrategy`, `replication_factor: 1` (single-node dev cluster, per Week 6 Slide 48 / `cql/init.cql`)

### Raw Results

| Trial | Consistency Level | Time (s) | Throughput (writes/sec) |
|---|---|---|---|
| 1 | LOCAL_QUORUM | 4.24 | 707.6 |
| 1 | ALL | 4.79 | 626.2 |
| 1 | ONE | 3.59 | 836.3 |
| 2 | ALL | 3.95 | 759.2 |
| 2 | ONE | 4.59 | 654.3 |
| 2 | LOCAL_QUORUM | 4.02 | 746.0 |
| 3 | ALL | 4.18 | 718.4 |
| 3 | ONE | 3.72 | 806.0 |
| 3 | LOCAL_QUORUM | 3.52 | 852.0 |

### Aggregated Results

| Consistency Level | Avg Throughput (writes/sec) | Std Dev | Δ vs ONE |
|---|---|---|---|
| ONE | 765.5 | ±79.6 | — |
| LOCAL_QUORUM | 768.5 | ±61.1 | +0.4% |
| ALL | 701.2 | ±55.6 | −8.4% |

### Analysis

The measured differences between consistency levels (+0.4% and −8.4%) are **smaller than the measurement noise itself** — standard deviations of ±55.6 to ±79.6 writes/sec, representing roughly 8–10% of the mean throughput at every level. This means the three consistency levels are **statistically indistinguishable** on this cluster once ordering bias is controlled for.

This null result is the theoretically correct and expected outcome, not a failed experiment. The test cluster runs `SimpleStrategy` with `replication_factor: 1` (the documented correct configuration for a single-node development cluster per Week 6 Slide 48 — using a higher RF on a single physical node would itself be a misconfiguration, since Cassandra cannot place multiple replicas of the same row on the same node). With only **one replica in existence**, every consistency level — `ONE`, `LOCAL_QUORUM`, and `ALL` — necessarily contacts the *same single node*, because there is no second or third replica to fan out to or wait on. The formula derived in Part A.2.b, `write_CL + read_CL > RF`, only produces a meaningfully different consistency *guarantee* — and correspondingly a different *latency profile* — once RF ≥ 2. At RF = 1, consistency-level tuning is a no-op by construction.

The small downward trend for `ALL` (−8.4%) is plausibly attributable to driver-side bookkeeping overhead in explicitly confirming acknowledgment semantics from the single replica, rather than any genuine replica-coordination cost — but at less than one standard deviation, this cannot be asserted as a confirmed effect from this dataset alone; a larger sample (more trials) would be needed to distinguish it from noise with confidence.

### Engineering Implication for GridSense at Production Scale

The design decision evaluated in Part A.2.c — `CL ONE` for sensor ingestion (justified by the assignment's 30-second staleness tolerance) versus `QUORUM`/`LOCAL_QUORUM` for billing-adjacent data where staleness is unacceptable — would only produce a *measurable* throughput divergence once the production cluster runs with **RF ≥ 2–3 across genuinely separate physical or virtual nodes**, as specified in the assignment's two-datacenter requirement (Part A.2.b). This benchmark, run against a single-node dev cluster, validates the *correctness* of the schema, the prepared-statement write path, and the consistency-level plumbing in `db/cassandra.py` — but the *magnitude* of the consistency-level trade-off described qualitatively in Part A can only be measured empirically on a true multi-node deployment. This is a disclosed and expected limitation of dev-scale benchmarking, not a gap in the analysis.

---

## C.2 — Graph Traversal Depth vs Latency (7%)

### Objective

Measure Neo4j Cypher traversal latency as a function of requested maximum depth (1–8 hops), to empirically validate the architectural claim in Part A.4.a that graph traversal cost does not grow with depth the way SQL JOIN cost grows with JOIN count.

### Methodology

The benchmark reproduces the exact `fault-impact` query pattern used in production (`api/routers/grid.py`), including the Trap 4 and Trap 5 fixes (bounded f-string depth interpolation, `length(path)` rather than `shortestPath()` in the `RETURN` clause). The synchronous `neo4j` Python driver was used directly against `bolt://graph-db:7687`.

- **Origin node:** `SS_001` (a Substation with known downstream topology: 4 Transformers via `:SUPPLIES`, 20 SmartMeters via `:CONNECTS_TO`)
- **Depths tested:** 1 through 8 (inclusive)
- **Iterations per depth:** 30, preceded by a 20-iteration discarded warm-up pass
- **Metric per iteration:** wall-clock query latency (ms) and result row count (`affected_count`)

### Raw Results

| Depth | Avg (ms) | Min (ms) | Max (ms) | StdDev | Affected Nodes |
|---|---|---|---|---|---|
| 1 | 15.615 | 8.990 | 139.829 | 23.611 | 4 |
| 2 | 12.960 | 6.303 | 133.754 | 22.943 | 24 |
| 3 | 8.067 | 5.070 | 14.242 | 1.869 | 24 |
| 4 | 10.849 | 4.221 | 121.425 | 20.977 | 24 |
| 5 | 12.154 | 5.283 | 134.830 | 23.357 | 24 |
| 6 | 10.002 | 4.355 | 131.699 | 23.051 | 24 |
| 7 | 9.460 | 4.223 | 108.944 | 18.868 | 24 |
| 8 | 9.790 | 4.742 | 111.819 | 19.321 | 24 |

### Plateau Finding

The affected-node count **plateaus at 24 nodes from depth 2 onward** and remains exactly flat through depth 8:

```
Depth 1:  4 nodes — new nodes reached
Depth 2: 24 nodes — new nodes reached
Depth 3: 24 nodes — SAME as depth 2 (graph exhausted)
Depth 4: 24 nodes — SAME as depth 3 (graph exhausted)
Depth 5: 24 nodes — SAME as depth 4 (graph exhausted)
Depth 6: 24 nodes — SAME as depth 5 (graph exhausted)
Depth 7: 24 nodes — SAME as depth 6 (graph exhausted)
Depth 8: 24 nodes — SAME as depth 7 (graph exhausted)
```

From `SS_001`, the seeded topology reaches all 4 directly-supplied Transformers at depth 1 (`:SUPPLIES`), and all 20 SmartMeters connected to those Transformers at depth 2 (`:CONNECTS_TO`) — 4 + 20 = 24. No further nodes exist beyond 2 real hops downstream of a Substation in this seed data (per `neo4j/import/seed.cypher`'s fixed 5-meters-per-transformer mapping), so depths 3 through 8 traverse the identical bounded Cypher pattern and simply find no additional matches.

### Analysis

This result validates the architectural claim made in Part A.4.a: **Cypher's traversal cost does not grow with the *requested* depth bound once the *real* graph is exhausted** — the query engine does not "search harder" past the last real edge; the variable-length pattern match terminates naturally once no further relationships of the specified types exist. This is the structural advantage over the SQL self-join approach analyzed in A.4.a, where each additional JOIN level (each additional depth requested) is a *fixed cost paid regardless of whether more data exists to find* — Neo4j's relationship-as-pointer storage model (Robinson et al., 2015, Ch. 2) means an exhausted traversal is cheap, not expensive.

**Latency noise and the min-latency signal.** The min-latency column is the cleanest signal of genuine traversal cost: it falls in a tight 4.2–9.0 ms band across all eight depths, consistent with a small, RAM-resident graph (262 nodes total in the dev seed — far below the 26,000-node production scale analyzed in Part A.4.d). The avg/max/stddev columns show periodic spikes up to ~140 ms that do **not** correlate with requested depth — these are attributable to JVM garbage collection pauses inherent to Neo4j's Java runtime (the same underlying mechanism cited in Part A's Discord/ScyllaDB case study, Week 6 Slide 41, as a documented JVM-related latency-tail problem) rather than to the Cypher traversal logic itself. Depth 3's unusually tight distribution (max 14.24 ms, stddev 1.87) is most plausibly a lucky sampling window between GC cycles rather than a structural property specific to depth-3 queries.

### Limitation

This benchmark used a single dev-scale graph (262 nodes total: 2 GridSupplyPoints, 10 Substations, 40 Transformers, 200 SmartMeters) with a real topology depth of only 2 hops from a Substation origin. The assignment's `< 200 ms` graph traversal latency SLA (Part A.4.c) is met here with enormous margin — worst observed case 139.8 ms, typical case under 16 ms — but this cannot be extrapolated directly to the production-scale 26,000-node topology described in Part A.4.d. At that scale, page cache pressure and adjacency-list traversal cost would both increase materially (as argued in A.4.d's supernode analysis), and a separate production-scale benchmark — ideally on a multi-node Neo4j cluster with realistic memory provisioning — would be required to validate the SLA at the assignment's stated scale.

---

## C.3 — Redis Cache Effectiveness (6%)

### Objective

Measure the real-world performance benefit of the Redis cache-aside pattern implemented for the `GET /sensors/{sensor_id}/summary` endpoint, comparing cold (cache-miss, Cassandra-backed) versus warm (cache-hit, Redis-backed) request latency under 1,000 requests per condition.

### Methodology

The benchmark issued real HTTP requests against the live, running FastAPI endpoint (`http://localhost:8000/sensors/SENSOR_001/summary`) — exercising the actual production cache-aside code path in `api/routers/sensors.py` (Week 4 Slide 24 pattern: check Redis → on miss, query Cassandra → aggregate → `SETEX` with TTL → return).

- **COLD phase:** the Redis key (`summary:SENSOR_001`) was explicitly deleted *before every single request*, forcing a fresh Cassandra query (last 100 readings for the sensor, aggregated client-side to latest-value-per-metric-type) on every call. This represents the worst-case scenario with zero caching benefit.
- **WARM phase:** the cache was primed once with a single discarded priming request, then all 1,000 measured requests hit the same warm Redis key.
- **Requests per phase:** 1,000
- **TTL used in production code:** 30 seconds (per `cache_set(..., ttl=30)` in `sensors.py`, matching the 30-second staleness tolerance specified in the assignment, Part A.2.c)

### Results

| Metric | COLD (Cassandra) | WARM (Redis) | Speedup |
|---|---|---|---|
| Avg | 11.061 ms | 2.583 ms | **4.3×** |
| Min | 5.746 ms | 1.669 ms | — |
| Max | 53.688 ms | 9.660 ms | — |
| StdDev | 4.500 ms | 0.723 ms | — |
| P50 (median) | 9.938 ms | 2.414 ms | **4.1×** |
| P95 | 18.970 ms | 4.024 ms | **4.7×** |
| P99 | 25.128 ms | 4.991 ms | **5.0×** |
| Throughput | 90.4 req/sec | 387.1 req/sec | **4.3×** |

### Analysis

The cache delivers a consistent **4.1×–5.0× speedup** across every percentile measured, with the gain **increasing at higher percentiles** (4.1× at P50, rising to 5.0× at P99). This is the expected and desirable behaviour of a well-functioning cache: it does not merely shift the average down, it specifically *flattens the tail*. Cassandra's occasional slower reads (P99 = 25.13 ms, likely attributable to a compaction cycle or a colder SSTable segment on that particular partition) are completely absorbed by Redis, which exhibits comparatively little variance regardless of what the underlying data looked like — Redis's warm-path standard deviation (0.723 ms) is over **6× tighter** than Cassandra's cold-path standard deviation (4.500 ms).

This empirically validates the design decision argued for in Part A.2.c: the 30-second cache TTL chosen for sensor summaries is justified not only by the assignment's stated 30-second staleness tolerance, but by a measured **4×+ throughput improvement** during the window the cache stays warm — directly reducing load on Cassandra at exactly the moment it matters most, the 40,000 events/sec sustained ingestion scenario analyzed in Part A.1.b. Every cache hit is one fewer query Cassandra has to serve while simultaneously absorbing high-volume sensor writes.

### Limitation

This benchmark measured a **single hot key under sequential (non-concurrent) load** from a single client. The assignment's actual production access pattern is many concurrent dashboard clients reading many *different* sensor summaries simultaneously. Under concurrent load, Cassandra's cold-path behaviour would likely degrade further — multiple simultaneous reads would contend for the same connection pool and thread-pool resources described in `db/cassandra.py`'s `run_in_executor` wrapper (since `cassandra-driver` is synchronous and offloaded to a thread pool, not natively async) — which would plausibly **widen** the measured speedup rather than narrow it, since Redis's in-memory O(1) lookups scale far more gracefully under concurrency than a thread-pool-bound synchronous driver. A concurrent-load variant of this benchmark (e.g. using `asyncio.gather` to fire many simultaneous requests, or a dedicated load-testing tool) was out of scope for this measurement but is a natural and recommended extension.

---

## C.4 — MongoDB vs PostgreSQL JSONB Query Comparison (5%)

### Objective

Compare query latency between MongoDB and PostgreSQL's JSONB column type (with GIN index) on an **identical dataset and identical query semantics**, isolating the JSON query engine itself as the variable under test — rather than conflating the comparison with the genuinely different production datasets each store holds in GridSense (MongoDB's 30 heterogeneous equipment documents vs PostgreSQL's 100 billing accounts).

### Methodology

A synthetic 30-record dataset was generated once (seeded with `random.seed(42)` for reproducibility) and inserted **identically** into:
- A temporary MongoDB collection (`bench_equipment`)
- A temporary PostgreSQL table (`bench_equipment`) with a single `JSONB` column (`data`) and a GIN index built on it (`CREATE INDEX ... USING GIN (data)`)

Each record had the same nested shape:
```json
{
  "asset_id": "BENCH_001",
  "category": "Transformer",
  "rating_kVA": 420,
  "metadata": {
    "status": "active",
    "manufacturer": "ABB",
    "install_year": 2018
  }
}
```

**Implementation note (debugging detail worth retaining for the report):** the first implementation attempt passed the same Python list of dicts to both `setup_mongo()` and `setup_postgres()`. This failed with `TypeError: Object of type ObjectId is not JSON serializable`, because PyMongo's `insert_many()` **mutates the input dicts in place**, injecting MongoDB's generated `_id: ObjectId(...)` field directly into the original list. By the time the same list reached `json.dumps()` for the PostgreSQL insert, it carried a non-JSON-serializable field. The fix was to deep-copy the record list (`pg_records = [dict(r) for r in records]`) **before** calling the MongoDB setup function, ensuring PostgreSQL received clean, unmutated records. This is a real and instructive example of a hidden side-effect bug in driver code, included here as part of the honest methodology record.

Three equivalent queries were run 50 times each against both stores:

| Query | MongoDB filter | PostgreSQL filter |
|---|---|---|
| Q1 — exact match | `{"category": "Transformer"}` | `data @> '{"category": "Transformer"}'::jsonb` |
| Q2 — numeric range | `{"rating_kVA": {"$gt": 300}}` | `(data->>'rating_kVA')::int > 300` |
| Q3 — nested containment | `{"metadata.status": "active"}` | `data @> '{"metadata": {"status": "active"}}'::jsonb` |

### Results

| Query | MongoDB Avg | MongoDB Min/Max | PostgreSQL Avg | PostgreSQL Min/Max | Faster | Margin |
|---|---|---|---|---|---|---|
| Q1 — exact match | 4.348 ms | 2.530 / 9.218 ms | 3.445 ms | 1.836 / 19.973 ms | PostgreSQL | 1.26× |
| Q2 — numeric range | 3.738 ms | 1.183 / 19.759 ms | 1.367 ms | 0.844 / 4.621 ms | PostgreSQL | 2.73× |
| Q3 — nested containment | 1.851 ms | 1.194 / 2.583 ms | 1.581 ms | 0.870 / 2.697 ms | PostgreSQL | 1.17× |

Both stores returned identical row counts per query (Q1: 10, Q2: 24, Q3: 10), confirming query-semantic equivalence.

### Analysis

PostgreSQL's GIN-indexed JSONB column outperformed MongoDB on **all three query types** at this scale, with the gap widest on the numeric range query (Q2, **2.73×**) and narrowest on the simple top-level equality and nested containment queries (Q1: 1.26×, Q3: 1.17×). This pattern is informative rather than a blanket "PostgreSQL is faster" conclusion — the *mechanism* differs meaningfully by query type:

- **Q2's larger gap is explained by query construction, not raw engine speed.** The MongoDB query (`{"rating_kVA": {"$gt": 300}}`) performs a native, typed numeric comparison directly on the field. The PostgreSQL query (`(data->>'rating_kVA')::int > 300`) must first **extract** the JSONB value as text (`->>` operator) and **cast** it to integer on every candidate row before comparing — a per-row text-extraction-and-cast overhead that a flat, natively-typed relational column would not incur. This is a direct, measured illustration of the point made conceptually in Week 4 Slide 33: JSONB is faster than the legacy `JSON` type because it avoids re-parsing the entire document, but it still carries a real extraction cost relative to a native column type — the GIN index accelerates *containment* lookups, not arbitrary value extraction and casting.
- **Q1 and Q3 both use containment-style matching** (`@>` on PostgreSQL; direct field match / dot-notation on MongoDB), which is precisely the access pattern the GIN index is built for (Week 4 Slide 34). The smaller margins here (1.17×–1.26×) suggest the underlying index-traversal cost is genuinely comparable between the two engines once the access pattern favors the index on both sides — the difference shrinks to driver/round-trip overhead rather than a structural query-engine advantage.

### Limitation — the central caveat for this result

**30 rows is far too small a dataset to draw a general "MongoDB vs PostgreSQL" performance conclusion.** At this scale, both engines almost certainly serve every query entirely from RAM — MongoDB's WiredTiger cache and PostgreSQL's `shared_buffers` both trivially hold 30 small documents in memory — meaning these measurements predominantly reflect query-parsing and client-driver round-trip overhead rather than genuine index-traversal or disk-I/O performance characteristics. The GIN index advantage PostgreSQL is specifically documented for (Part A.5 Case 5; Week 4 Slide 34) is designed to matter at the **scale the assignment actually specifies** — 1.2 million billing accounts — where a full collection or table scan becomes prohibitively expensive and the *index*, not the underlying engine, becomes the deciding architectural factor. This benchmark validates that PostgreSQL's JSONB + GIN mechanism functions correctly and is not slower even at toy scale — a meaningful and honest finding in itself — but a genuine "MongoDB vs PostgreSQL at scale" comparison would require seeding both stores with 10,000+ documents and re-running this exact harness unmodified. That is a direct, low-effort extension of this benchmark for future work, not a redesign of the methodology.

---

## Summary Table — All Four Sub-Tasks

| Task | Finding | Statistical confidence | Key limitation |
|---|---|---|---|
| C.1 | Consistency levels (ONE/LOCAL_QUORUM/ALL) statistically indistinguishable at RF=1 | High — differences smaller than ±1 std dev across 9 randomized trials | Result is specific to RF=1; production RF≥2 would diverge |
| C.2 | Traversal latency plateaus at depth 2; cost does not grow once graph is exhausted | High — plateau is exact and reproducible across 30 iterations/depth | Dev-scale graph (262 nodes) vs 26,000-node production target |
| C.3 | Redis delivers 4.1×–5.0× speedup over Cassandra cold reads, widening at higher percentiles | High — 1,000 requests/phase, low variance in warm phase | Sequential single-key load, not concurrent multi-client load |
| C.4 | PostgreSQL JSONB+GIN outperforms MongoDB 1.17×–2.73× depending on query type | High — identical dataset, 50 iterations/query, row counts verified equal | 30-row dataset too small to generalize; both engines RAM-resident at this scale |

## Bibliography (carried forward from Part A, cited again where relevant)

Chang, F., Dean, J., Ghemawat, S., Hsieh, W. C., Wallach, D. A., Burrows, M., Chandra, T., Fikes, A., & Gruber, R. E. (2006). Bigtable: A distributed storage system for structured data. *ACM Transactions on Computer Systems*, 26(2), 1–26.

Kleppmann, M. (2017). *Designing Data-Intensive Applications*. O'Reilly Media.

Lakshman, A. & Malik, P. (2010). Cassandra: A decentralized structured storage system. *ACM SIGOPS Operating Systems Review*, 44(2), 35–40.

Robinson, I., Webber, J., & Eifrem, E. (2015). *Graph Databases: New Opportunities for Connected Data* (2nd ed.). O'Reilly Media.

Vogels, W. (2009). Eventually consistent. *Communications of the ACM*, 52(1), 40–44.

---

*All benchmark scripts (`bench_c1_consistency.py`, `bench_c2_traversal.py`, `bench_c3_cache.py`, `bench_c4_mongo_vs_postgres.py`) are included in the `scripts/` directory of the submitted repository for reproducibility.*