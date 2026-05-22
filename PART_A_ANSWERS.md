# GridSense — Part A: Critical Thinking Answers

> **Usage:** These are the finalized answers for Part A of the report. When writing the final PDF report, copy and expand these answers into the required Calibri 11pt, 1.15 spacing format.

---

## A.1 — Why Relational Databases Were Not Enough (5%)

### A.1.a — The Genuine Merit in the Senior Engineer's Argument

The senior engineer's argument deserves a serious response, not dismissal. His core claim — that a single, well-configured relational system avoids the operational complexity of polyglot persistence — is technically defensible for a significant class of workloads.

PostgreSQL with the TimescaleDB extension is a legitimate time-series platform. TimescaleDB's hypertables partition data automatically by time, compressing old chunks and maintaining query performance at moderate scale. For sensor deployments in the range of a few thousand devices reporting at intervals of several minutes, PostgreSQL handles the workload while offering full ACID guarantees, a mature ecosystem, and a single operational surface for backup, monitoring, and staffing. The "training debt" concern is equally real: operating five different storage systems requires engineers fluent in Cassandra tuning, Neo4j memory management, MongoDB index design, Redis eviction policy, and PostgreSQL query planning simultaneously. Sadalage and Fowler (2012) call this the central tension of polyglot persistence — the benefits of using the right tool for each job are real, but so is the organisational cost of maintaining expertise across fundamentally different consistency and query models. In a utility organisation staffed primarily with electrical engineers, this is a genuine risk.

He is also correct that PostgreSQL's JSONB with GIN indexing genuinely solves the flexible-schema problem for equipment metadata without a separate document store. That point, in isolation, holds.

### A.1.b — Where the Argument Breaks Down for GridSense

The argument collapses at the sensor ingestion requirement. Section 2.1 states 40,000 events per second at normal load, rising to 120,000 during storm events.

**Concrete calculation:** At 40,000 events/second, assuming a compact row of 80 bytes per reading (sensor_id 20 bytes + timestamp 8 bytes + metric_type 10 bytes + value 4 bytes + overhead), the sustained write throughput is:

```
40,000 events/sec × 80 bytes = 3.2 MB/sec sustained
Peak (storm): 120,000 × 80 bytes = 9.6 MB/sec
Over 24 hours at normal load: 3.2 MB/s × 86,400 s ≈ 276 GB/day
Over 90 days: ≈ 24.8 TB
```

Kleppmann (2017, Ch. 3) explains that PostgreSQL's heap-based storage model means every INSERT physically appends a new tuple to a heap file and writes a corresponding WAL (Write-Ahead Log) record — so each event is written at least twice on disk before acknowledgement. At 40,000 inserts/sec this generates a sustained WAL write of approximately 6.4 MB/sec. Compounding this, PostgreSQL's MVCC implementation (Kleppmann, 2017, Ch. 7) retains old row versions to support concurrent readers; in a pure-insert time-series workload there are no updates, but transaction ID wraparound eventually forces a VACUUM FREEZE that briefly blocks new writes. Benchmarks on commodity NVMe hardware show a single PostgreSQL instance sustaining approximately 50,000–80,000 simple inserts/sec under ideal conditions — no concurrent reads, no secondary indexes, no autovacuum activity. With the sensor table indexed on sensor_id and reading_time (necessary for the required dashboard queries), index maintenance alone reduces the practical ceiling to roughly 20,000–30,000 inserts/sec. GridSense requires 40,000 at sustained load and 120,000 at peak — before the billing, equipment, and topology queries run concurrently on the same instance.

Furthermore, Kleppmann (2017, Ch. 6) establishes that distributing a relational database to handle this volume reintroduces the JOIN problem across nodes: a query spanning two shards requires network round-trips and distributed locking that destroys the sub-50 ms write latency guarantee.

### A.1.c — When a PostgreSQL-Only Design Would Be Correct

A PostgreSQL-only architecture becomes the correct engineering decision when the following conditions hold simultaneously:

- **Write rate below ~10,000 events/sec** — within PostgreSQL's sustained insert capacity on a well-tuned instance with time-based table partitioning
- **Total data volume below ~5 TB** — manageable with native partitioning, no multi-node horizontal scaling required
- **Team size below ~5 engineers** — where the operational cost of polyglot persistence exceeds its value, as Sadalage and Fowler (2012) explicitly acknowledge
- **Strong consistency required everywhere** — if billing-class ACID guarantees are needed for sensor data, for example in a regulatory environment treating every reading as a legally binding record
- **No deep graph traversal requirement** — if fault propagation can tolerate recursive CTE queries (`WITH RECURSIVE`) at shallow depth, which PostgreSQL handles adequately up to 3–4 hops before query planning degrades significantly

For a municipal utility managing under 5,000 sensors at 5-minute intervals, this describes the correct stack. GridSense operates at 3.4 million sensors at one-second intervals — three orders of magnitude beyond the PostgreSQL-only sweet spot.

---

## A.2 — The CAP Theorem Applied (7%)

### A.2.a — The Precise Trade-off Being Made

Gilbert and Lynch (2002) define the three properties formally:

- **Consistency (C):** Every read operation on a distributed storage object returns the most recently written value, or an error — across all nodes simultaneously.
- **Availability (A):** Every request received by a non-failed node must result in a response — the system cannot return an error or refuse to answer.
- **Partition Tolerance (P):** The system continues to operate even when an arbitrary number of messages between nodes are lost or delayed by the network.

The critical clarification that the popularised version omits: the theorem does not present a permanent three-way choice. As Kleppmann (2017, Ch. 9) argues, **Partition Tolerance is not genuinely optional in any distributed system deployed over real infrastructure** — network partitions are not hypothetical; they are a statistical certainty at the node counts and uptimes GridSense requires. A system that claims to sacrifice P is simply a single-node system that has not admitted it yet.

The real choice is therefore: **when a partition occurs, do you halt and preserve consistency, or do you continue and accept stale data?**

Engineer A (CL ONE / CL ONE) chooses **availability during partitions**: Cassandra acknowledges the write after a single replica confirms it and serves reads from the first responding replica without verifying it holds the latest version. If that replica is behind due to replication lag, the client receives stale data — no error is returned.

Engineer B (QUORUM / QUORUM) chooses **consistency during partitions**: a majority of replicas must confirm both write and read. If enough replicas are unavailable to form a quorum, the operation fails rather than returning stale data. Partition tolerance is maintained in the sense that the cluster does not crash — but some operations return errors, trading away full availability.

The trade-off is squarely within the C vs A dimension of CAP, with P held constant.

### A.2.b — Minimum Consistency Levels for RF = 3 Across Two Datacenters

The rule for guaranteed strong consistency in Cassandra is (Kleppmann, 2017, Ch. 5):

```
write_CL + read_CL > Replication Factor
```

With RF = 3:

| Write CL | Replicas Written | Read CL | Replicas Read | Sum | Consistent? |
|---|---|---|---|---|---|
| ONE | 1 | ONE | 1 | 2 | ✗ (2 ≤ 3) |
| ONE | 1 | ALL | 3 | 4 | ✓ but write ONE is fragile |
| QUORUM | 2 | QUORUM | 2 | 4 | ✓ (4 > 3) |
| ALL | 3 | ONE | 1 | 4 | ✓ but ALL write fails on any node loss |

**Minimum balanced configuration: QUORUM writes + QUORUM reads.**

QUORUM for RF = 3 = ⌊RF/2⌋ + 1 = ⌊3/2⌋ + 1 = 1 + 1 = **2 replicas**. Two replicas must confirm the write; two must confirm the read. Since at most one replica can be behind — only one node does not participate in either quorum — any read is guaranteed to include at least one node that confirmed the most recent write.

For a two-datacenter deployment, the preferred configuration is **LOCAL_QUORUM** for both reads and writes. With RF = 3 distributed as 2 replicas in DC1 and 1 replica in DC2:

- LOCAL_QUORUM in DC1 requires both DC1 replicas to respond — write must hit both DC1 nodes
- LOCAL_QUORUM read in DC1 also requires 2 nodes — guaranteed to overlap with the write quorum
- This guarantees consistency within DC1 without incurring cross-datacenter latency on every operation, while replicating asynchronously to DC2 for geographic durability

### A.2.c — Which Engineer Is Making the Better Decision for Sensor Ingestion

**Engineer A is making the better engineering decision for sensor ingestion specifically.**

The stated requirements from Section 2.2 are:

- Write latency: acknowledged < 50 ms
- Consistency for sensors: *"Sensor counters and dashboard aggregates may lag up to 30 seconds"*

The 30-second lag tolerance is not eventual consistency as a fallback — it is the stated design intent. Vogels (2009) defines eventual consistency precisely as a model where, in the absence of new updates, all replicas will converge to the same value, with the convergence window determined by application requirements rather than the database. A 30-second convergence window is a generous bound; CL ONE on a healthy cluster typically converges within milliseconds. CL ONE therefore satisfies the stated requirement with substantial margin.

CL QUORUM would require two of three replicas to acknowledge every write. In a two-datacenter deployment, cross-datacenter latency alone can push individual write acknowledgements toward 30–80 ms. At 40,000 events/second, this coordination overhead threatens the < 50 ms write SLA. For billing operations — where Section 2.2 states data loss is unacceptable — Engineer B's position is correct. Applying QUORUM uniformly to sensor data sacrifices the system's core throughput requirement without providing a consistency benefit the use case actually needs.

### A.2.d — The PACELC Dimension

PACELC (Abadi, 2012) extends CAP by observing that CAP only characterises behaviour **during network partitions (P)**. Since partitions are rare in well-managed infrastructure, this leaves most of a system's operational life uncharacterised. The theorem adds: **Even in the absence of a partition (E), a distributed system faces a trade-off between Latency (L) and Consistency (C)** — because achieving consistency always requires inter-node coordination, and coordination takes time regardless of partition status.

The PACELC classification relevant here:

| System | During Partition | Else (no partition) |
|---|---|---|
| Cassandra (default) | PA — prefers Availability | EL — prefers Low Latency |
| HBase | PC — prefers Consistency | EC — prefers Consistency |

For a 24/7 grid monitoring system where partitions are rare but operator response time is critical, Abadi's (2012) additional dimension is precisely the right lens:

- **Cassandra's PA/EL profile is the correct fit for sensor ingestion.** When no partition exists — the normal case — Cassandra with CL ONE delivers sub-millisecond write latency because there is no coordination overhead. Kleppmann (2017, Ch. 9) notes that even small coordination delays compound into significant tail latency at high request rates — exactly the GridSense sensor ingestion pattern at 40,000 events/sec. The current 22-minute fault diagnosis baseline cannot tolerate systematic database-induced delays accumulating across millions of events.
- **HBase's PC/EC profile is the correct choice when the cost of a stale read is operationally or legally significant** — for example, the relay event log, where a forensic query returning an event out of order could compromise a legal investigation. But for the raw sensor stream feeding dashboards where 30-second staleness is explicitly acceptable, HBase's consistency overhead is an unnecessary tax on latency.

---

## A.3 — Column-Family Data Modelling (6%)

### A.3.a — Table Schema Optimised for Pattern (1): Last N Readings per Sensor

Query pattern: *retrieve the last N readings for a given sensor within a time window.*

```sql
CREATE TABLE sensor_readings (
    sensor_id    TEXT,
    reading_time TIMESTAMP,
    metric_type  TEXT,
    value        FLOAT,
    unit         TEXT,
    quality_flag INT,
    PRIMARY KEY ((sensor_id), reading_time, metric_type)
) WITH CLUSTERING ORDER BY (reading_time DESC, metric_type ASC)
AND default_time_to_live = 7776000;  -- 90 days
```

**Partition key — `sensor_id`:**
The column-family model originates in Google's Bigtable (Chang et al., 2006), which introduced the row key as the primary distribution and co-location mechanism. Cassandra inherits this directly (Lakshman & Malik, 2010): the partition key is hashed using MurmurHash3 and the resulting token determines which node owns that row. All rows sharing the same `sensor_id` are therefore co-located on the same node and the same set of disk pages. A query for "last 10 readings of SENSOR_042" is a single partition read — one disk seek, no cross-node coordination.

**Clustering key — `(reading_time DESC, metric_type ASC)`:**
Chang et al. (2006) describe the SSTable (Sorted String Table) format as a sorted, immutable on-disk structure where keys within a tablet are physically ordered. Cassandra inherits this: within a partition, rows are sorted on disk by the clustering key. With `reading_time DESC`, the most recent readings are physically first — returning the last N readings is a sequential read from the front of the SSTable segment rather than a sort operation. The `metric_type` secondary clustering key prevents Last-Write-Wins data loss: if a sensor emits `voltage`, `current`, and `power_factor` at the same millisecond timestamp, each gets a distinct cell rather than the last write silently overwriting the others. Without `metric_type` in the clustering key, two of three simultaneous metric readings would be permanently lost.

**Physical implication:** Each partition holds all readings for one sensor. At one reading per second across 4 metric types, a sensor accumulates 4 × 86,400 × 90 = 31.1 million rows over 90 days. The TTL setting ensures automatic expiry without explicit deletes.

### A.3.b — Why Pattern (2) Cannot Be Served Efficiently

Pattern (2): *retrieve all sensor readings across the entire network in the last 60 seconds.*

The query a developer would attempt:

```sql
SELECT * FROM sensor_readings
WHERE reading_time > toTimestamp(now()) - 60000
ALLOW FILTERING;
```

`ALLOW FILTERING` is required because `reading_time` is a clustering key, not a partition key. Kleppmann (2017, Ch. 6) explains that in a partitioned database, a filter on a non-partition column requires the coordinator to scatter the request to every node and gather results — an O(N) operation across all partitions. With 3.4 million sensors each having their own partition, this triggers 3.4 million independent partition reads simultaneously.

The deeper problem is architectural: Cassandra distributes data by partition key hash; there is no global time-ordered index across partitions. Each node holds a random subset of sensor partitions, so reconstructing a time-ordered view of all sensors requires aggregating from every node, applying a heap merge on potentially hundreds of millions of rows, and returning the result within the < 100 ms P95 latency requirement — physically impossible with this schema.

### A.3.c — Second Table for Pattern (2) and Write Amplification Analysis

Following the query-driven design principle from Bigtable (Chang et al., 2006) — carried directly into Cassandra's design philosophy by Lakshman and Malik (2010) — one table per query pattern:

```sql
CREATE TABLE sensor_readings_by_time (
    time_bucket  TEXT,
    reading_time TIMESTAMP,
    sensor_id    TEXT,
    metric_type  TEXT,
    value        FLOAT,
    PRIMARY KEY ((time_bucket), reading_time, sensor_id, metric_type)
) WITH CLUSTERING ORDER BY (reading_time DESC);
```

Pattern (2) query:
```sql
SELECT * FROM sensor_readings_by_time
WHERE time_bucket = '2026-05-12T10:05'
AND reading_time > toTimestamp(now()) - 60000;
```

This is now a single partition read — all readings in the current minute are co-located.

**Write amplification analysis:**
Every inbound sensor event writes to two tables instead of one:

```
Normal load:  40,000 writes/sec × 2 = 80,000 writes/sec
Peak (storm): 120,000 × 2          = 240,000 writes/sec
```

Kleppmann (2017, Ch. 3) defines write amplification as the ratio of data written to storage versus data logically produced by the application — a fundamental cost in any system that maintains multiple views of the same data. The amplification factor here is exactly 2. However, Cassandra's write path is a sequential append to CommitLog and MemTable (Chang et al., 2006), with no random I/O. Production Cassandra clusters routinely sustain 200,000+ sequential writes/sec per node, so 80,000 writes/sec across a properly sized cluster remains within operational bounds. The trade-off — doubling write amplification in exchange for O(1) dashboard reads — is the standard denormalisation decision the column-family model is explicitly designed to support.

### A.3.d — Partition Key Strategy to Prevent Hot Partitions

A `sensor_id`-only partition key creates two failure modes: unbounded partition growth for high-frequency sensors, and a hot partition when one sensor generates disproportionate write traffic to a single node. Kleppmann (2017, Ch. 6) identifies this as the "hot spot" problem and identifies time-based bucketing as the standard mitigation for time-series workloads.

**Proposed strategy: composite partition key with time bucketing.**

```sql
PRIMARY KEY ((sensor_id, date_bucket), reading_time, metric_type)
```

Where `date_bucket` is derived at write time as a date string (e.g., `'2026-05-12'` for daily buckets).

**Effect:** A sensor reporting every second spreads its data across 90 daily partitions over the retention window rather than one monolithic partition. Today's partition receives writes; yesterday's is cold and subject to compaction.

**Query preservation:** Retrieving the last N readings across day boundaries requires the application to enumerate the relevant buckets:

```sql
SELECT * FROM sensor_readings
WHERE sensor_id = 'SENSOR_042'
AND date_bucket IN ('2026-05-12', '2026-05-11')
ORDER BY reading_time DESC LIMIT 100;
```

This targets exactly two known partitions — not a cluster-wide scan. Kleppmann (2017, Ch. 6) frames this as an intentional design decision: the application takes on a small amount of routing logic in exchange for predictable, bounded partition sizes at any scale.

---

## A.4 — Graph Databases and the Network Topology Problem (7%)

### A.4.a — Why the Problem is Naturally Suited to a Graph Database

The fault propagation problem requires traversing a network of arbitrary depth following typed relationships between heterogeneous node types. Robinson, Webber, and Eifrem (2015, Ch. 1) identify this as the "connected data" problem: when the relationships between entities are as important as the entities themselves, the relational model's representation of connections as foreign keys and join tables becomes structurally inefficient.

**SQL query for a two-hop downstream traversal from substation SS_001:**

```sql
SELECT DISTINCT n2.node_id, n2.node_type, n2.name
FROM network_nodes n1
JOIN network_edges e1 ON n1.node_id = e1.from_id
    AND e1.type IN ('FEEDS', 'SUPPLIES', 'CONNECTS_TO')
JOIN network_nodes n2 ON e1.to_id = n2.node_id
WHERE n1.node_id = 'SS_001'

UNION

SELECT DISTINCT n3.node_id, n3.node_type, n3.name
FROM network_nodes n1
JOIN network_edges e1 ON n1.node_id = e1.from_id
    AND e1.type IN ('FEEDS', 'SUPPLIES', 'CONNECTS_TO')
JOIN network_nodes n2 ON e1.to_id = n2.node_id
JOIN network_edges e2 ON n2.node_id = e2.from_id
    AND e2.type IN ('FEEDS', 'SUPPLIES', 'CONNECTS_TO')
JOIN network_nodes n3 ON e2.to_id = n3.node_id
WHERE n1.node_id = 'SS_001';
```

**Equivalent Cypher:**

```cypher
MATCH (origin {node_id: 'SS_001'})-[:FEEDS|SUPPLIES|CONNECTS_TO*1..2]->(downstream)
RETURN DISTINCT downstream.node_id, labels(downstream)[0] AS node_type, downstream.name
```

**Complexity as depth increases:**

| Depth | SQL JOINs Required | Cypher pattern |
|---|---|---|
| 1 | 2 JOINs | `*1..1` |
| 2 | 4 JOINs + 1 UNION | `*1..2` |
| 4 | 8 JOINs + 3 UNIONs | `*1..4` |
| 6 | 12 JOINs + 5 UNIONs | `*1..6` |

The SQL query grows linearly in JOIN count with depth but degrades super-linearly in performance, because each JOIN expands the intermediate result set before de-duplication. At depth 6 across 26,000 network nodes, the Cartesian product before `DISTINCT` can exceed billions of intermediate rows. Robinson et al. (2015, Ch. 2) explain that the Cypher pattern stays syntactically constant because Neo4j's native storage model represents relationships as doubly linked list pointers adjacent to each node record on disk: traversal is a pointer-chase with O(degree) complexity per hop rather than an index lookup on a join table.

### A.4.b — Property Graph Model for the GridSense Network

Robinson et al. (2015, Ch. 2) define the property graph model as consisting of nodes (entities), relationships (typed and directed connections), and properties (key-value attributes on both nodes and relationships). The GridSense model follows this definition directly.

**Node labels and properties:**

| Label | Key Properties |
|---|---|
| GridSupplyPoint | gsp_id (unique), name, voltage_kV, region |
| Substation | substation_id (unique), name, voltage_kV, lat, lon, commissioned_year |
| Transformer | asset_id (unique), rating_kVA, manufacturer, model, installed (date), last_inspection (date) |
| SmartMeter | meter_id (unique), premise_id, tariff_class, phase |

**Relationship types and properties:**

| Relationship | From → To | Properties |
|---|---|---|
| :FEEDS | GridSupplyPoint → Substation | feeder_id, voltage_kV, length_km |
| :SUPPLIES | Substation → Transformer | cable_id, distance_m |
| :CONNECTS_TO | Transformer → SmartMeter | (none required) |
| :ALTERNATIVE_FEED | Substation → Substation | tie_switch_id, normally_open (boolean) |

**Why certain properties belong on relationships, not nodes:**

Robinson et al. (2015, Ch. 4) establish that properties belong on a relationship when they describe the connection itself rather than either endpoint. `feeder_id`, `voltage_kV`, and `length_km` describe the cable running between a GridSupplyPoint and a Substation — not either node individually. A substation may be fed by multiple feeders at different voltages; placing `voltage_kV` on the Substation node would require duplicating the value or introducing a normalisation table, reintroducing exactly the relational problem graph databases are designed to avoid. `tie_switch_id` on `:ALTERNATIVE_FEED` identifies a specific switchgear device that is normally open — it is an attribute of a particular electrical path between two substations, not of either substation individually.

### A.4.c — Neo4j as a CP System During Leader Election

In its clustered configuration, Neo4j 5 is a CP system under CAP: during a network partition or leader election, it prioritises consistency over availability. Kleppmann (2017, Ch. 5) describes single-leader replication systems as entering a period where writes are unavailable during failover — the cluster refuses write operations rather than risking split-brain inconsistency. For Neo4j, this window is bounded (typically seconds) but non-zero.

**Practical consequence for fault events:** A relay trip generates an event that must be written to both Neo4j (topology analysis) and Cassandra (the relay event log). If a Neo4j leader election is in progress when the fault is detected, the Neo4j write will be rejected. If the API propagates this error to the caller, the fault event is lost — unacceptable for a safety-critical system.

**Design mitigation using Redis as a durable buffer:**

1. The API publishes the fault event to a Redis Pub/Sub channel immediately upon receipt — Redis is an AP system and is always available during any Neo4j disruption
2. A background worker consumes from Redis and writes to Neo4j once the leader is re-established
3. Cassandra receives the relay event log entry independently via CL ONE — never blocked by Neo4j availability

This decouples fault capture from graph persistence. The capture path (API → Redis → Cassandra) does not depend on Neo4j at all; no event is missed. The graph topology update (Redis → Neo4j) is eventually consistent, with convergence bounded by the leader election window. This satisfies the stated constraint: fault event sequences are stored in strict causal order by Cassandra's TIMEUUID clustering key, while the < 200 ms graph traversal latency requirement applies to reads, not writes.

### A.4.d — Time-Series Data in Graph Databases: Where It Collapses

The proposal to store every sensor reading as a graph node creates what Robinson et al. (2015) call the **supernode problem**: a node that accumulates an unbounded number of relationships becomes a performance bottleneck regardless of the underlying storage engine.

**At small volume:** The design is workable up to roughly a few thousand reading nodes per sensor. A sensor with 1,000 readings has a `SmartMeter` node connected to 1,000 `Reading` nodes — traversal is fast.

**At GridSense scale:** A sensor reporting every second accumulates 86,400 × 90 = 7,776,000 `Reading` nodes over 90 days. With 3.4 million sensors:

```
7,776,000 nodes × 3,400,000 sensors = 26.4 trillion nodes
```

Kleppmann (2017, Ch. 3) explains that graph databases store nodes and relationships in fixed-size records designed for fast pointer-chasing. At 26.4 trillion nodes, even at 15 bytes per node record the raw node store alone exceeds 390 petabytes before properties, relationships, or indexes. More critically, Neo4j's page cache must hold the working set of relationship adjacency lists in memory for fast traversal; at this scale the working set cannot fit in any reasonable RAM allocation, forcing every traversal to disk and collapsing the sub-millisecond latency guarantee.

The practical boundary is approximately 10,000–100,000 child nodes per parent before adjacency list traversal becomes unpredictable (Robinson et al., 2015) — roughly 3–28 hours of one-second sensor data per device. The 90-day retention requirement exceeds this by two to three orders of magnitude. The separation of concerns in the GridSense architecture — topology in Neo4j, time-series in Cassandra — is not a convenience; it is a physical necessity.

---

## A.5 — Technology Selection Reasoning (5%)

### Case 1 — Store the Complete Sequence of Relay Operations

**Technology: Apache Cassandra**

The relay event log requires strict causal ordering, immutability after write, and efficient chronological replay. Cassandra's `TIMEUUID` clustering key embeds a 100-nanosecond-resolution timestamp directly in the key value — Chang et al. (2006) describe this pattern in the Bigtable context as providing natural ordering without external coordination. Partitioning by `feeder_id` and clustering by `event_time ASC` means a forensic replay of all events on a feeder is a single sequential partition scan requiring no in-flight sorting. Cassandra's append-only SSTable model (Lakshman & Malik, 2010) makes overwriting an existing record physically impossible — writes create new cells with higher timestamps rather than modifying existing data — satisfying the immutability requirement. PostgreSQL could store this data with ACID guarantees, but serialisable transactions on every insert at 40,000 events/sec would introduce coordination overhead incompatible with the < 50 ms write SLA.

### Case 2 — Find All Consumers Sharing a Distribution Transformer

**Technology: Neo4j**

The access pattern is a two-hop graph traversal: from a target `SmartMeter`, traverse backwards to the connected `Transformer`, then forward to all peer `SmartMeter` nodes. In Cypher:

```cypher
MATCH (:SmartMeter {meter_id: $target})<-[:CONNECTS_TO]-(t:Transformer)-[:CONNECTS_TO]->(peer:SmartMeter)
RETURN peer.meter_id, peer.premise_id
```

Robinson et al. (2015, Ch. 2) explain that Neo4j stores relationships as adjacency list pointers adjacent to each node record — traversal is a pointer-chase with no index lookup on a join table. In SQL this requires two self-joins on the relationships table; the pattern generalises to arbitrary depth without rewriting the query. No other technology in the stack serves this access pattern without application-level join logic.

### Case 3 — Cache the Overload Status of 22,000 Transformers with 5-Second Expiry

**Technology: Redis**

The access pattern is a keyed lookup with automatic expiry. Vogels (2009) defines TTL-based expiry as the fundamental mechanism for ensuring cached state does not outlive the underlying reality it represents — precisely the requirement here, where a transformer's overload status can change within seconds. Redis's `SETEX` command sets a value with a TTL atomically: `SETEX transformer:TX_001_A:status 5 "OVERLOADED"`. After 5 seconds, Redis evicts the key automatically — no background job, no scheduled DELETE. Lookup by key is O(1) with sub-millisecond latency from RAM. Storing 22,000 keys of this size requires less than 5 MB of RAM — negligible. No other technology in the stack provides automatic TTL-based expiry, O(1) lookup, and sub-millisecond latency in a single atomic primitive.

### Case 4 — Store a New Smart Meter with 40 Non-Standard Telemetry Fields

**Technology: MongoDB**

The access pattern requires storing a record whose schema is unknown at design time and unique to one manufacturer. Kleppmann (2017, Ch. 2) identifies schema-on-read as the appropriate model when different records in the same collection legitimately have different structures — exactly the case here, where each meter generation arrives with a different field set. Adding 40 new fields to a MongoDB document requires no `ALTER TABLE`, no schema migration, and no NULL columns across the existing 22,000 records. PostgreSQL's JSONB column could store the 40 fields in a single opaque column, but querying individual fields requires JSON path operators and forgoes column-level type validation. MongoDB's index system indexes individual document fields natively, and its aggregation pipeline addresses nested fields without path-operator syntax. The schema flexibility is not a convenience feature — it is the structural constraint that eliminates migration scripts when new meter generations are deployed.

### Case 5 — Monthly Billing Calculations for 1.2 Million Accounts

**Technology: PostgreSQL**

Monthly billing applies progressive tariff bands, regulatory surcharges, and time-of-use pricing adjustments — operations that must either fully complete across all 1.2 million accounts or not happen at all. Kleppmann (2017, Ch. 7) defines atomicity as the guarantee that a transaction's effects are all-or-nothing; a billing run that fails midway and leaves accounts invoiced at different tariff versions generates the regulatory penalties stated in Section 2.1. PostgreSQL's serialisable isolation guarantees that concurrent reads during month-end processing see a consistent snapshot of account balances. The `tariff_info JSONB` column stores each account's tariff structure as flexible structured data, queryable with the `@>` containment operator and accelerated by a GIN index. No eventually consistent store provides the atomicity guarantee that makes this use case safe.

---

## Bibliography

Abadi, D. J. (2012). Consistency tradeoffs in modern distributed database system design: CAP is only part of the story. *IEEE Computer*, 45(2), 37–42.

Chang, F., Dean, J., Ghemawat, S., Hsieh, W. C., Wallach, D. A., Burrows, M., Chandra, T., Fikes, A., & Gruber, R. E. (2006). Bigtable: A distributed storage system for structured data. *ACM Transactions on Computer Systems*, 26(2), 1–26.

Gilbert, S. & Lynch, N. (2002). Brewer's conjecture and the feasibility of consistent, available, partition-tolerant web services. *ACM SIGACT News*, 33(2), 51–59.

Kleppmann, M. (2017). *Designing Data-Intensive Applications*. O'Reilly Media.

Lakshman, A. & Malik, P. (2010). Cassandra: A decentralized structured storage system. *ACM SIGOPS Operating Systems Review*, 44(2), 35–40.

Robinson, I., Webber, J., & Eifrem, E. (2015). *Graph Databases: New Opportunities for Connected Data* (2nd ed.). O'Reilly Media.

Sadalage, P. J. & Fowler, M. (2012). *NoSQL Distilled: A Brief Guide to the Emerging World of Polyglot Persistence*. Addison-Wesley.

Vogels, W. (2009). Eventually consistent. *Communications of the ACM*, 52(1), 40–44.
