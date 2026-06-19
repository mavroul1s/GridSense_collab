# scripts/bench_c2_traversal.py
# Part C.2 — Graph traversal depth vs latency
# Depths 1-8, 30 iterations each = 240 measured queries + warm-up
#
# Methodology: uses the synchronous neo4j driver (simpler for a
# standalone benchmark script than the API's async driver).
# Each depth runs 30 iterations; min/max/avg/stddev reported.
#
# Topology note: seeded graph has a real max depth of 3 hops from
# a Substation origin (Substation -> Transformer -> SmartMeter via
# :SUPPLIES/:CONNECTS_TO, or up to GSP via :FEEDS). Depths beyond 3
# do not expand further — Cypher's upper-bound pattern simply finds
# no additional paths. This is itself a reportable finding: traversal
# cost should plateau once the real graph is exhausted.

import time
import statistics
from neo4j import GraphDatabase

URI  = "bolt://graph-db:7687"
AUTH = ("neo4j", "gridsense_neo4j_secret")  # matches .env NEO4J_PASSWORD

ORIGIN_NODE_ID = "SS_001"   # Substation with known downstream topology
N_ITERATIONS   = 30
DEPTHS         = list(range(1, 9))   # 1 through 8

DOWNSTREAM_RELS = "FEEDS|SUPPLIES|CONNECTS_TO"


def run_traversal(session, max_depth: int):
    """Execute the same fault-impact query pattern used in grid.py,
    f-string interpolation of a pre-validated bounded int (1-10),
    exactly as in production — Trap 4 fix applied here too."""
    cypher = f"""
        MATCH (origin {{node_id: $node_id}})
        MATCH path = (origin)-[:{DOWNSTREAM_RELS}*1..{max_depth}]->(downstream)
        RETURN count(downstream) AS affected_count
    """
    start = time.perf_counter()
    result = session.run(cypher, node_id=ORIGIN_NODE_ID)
    record = result.single()
    elapsed_ms = (time.perf_counter() - start) * 1000
    return elapsed_ms, record["affected_count"]


def warm_up(session, n=20):
    for _ in range(n):
        run_traversal(session, 3)
    print(f"Warm-up complete ({n} discarded queries)\n")


def main():
    driver = GraphDatabase.driver(URI, auth=AUTH)

    print("=" * 75)
    print("C.2 — Graph Traversal Depth vs Latency")
    print(f"Origin: {ORIGIN_NODE_ID} | {N_ITERATIONS} iterations per depth | depths 1-8")
    print("=" * 75)

    with driver.session(database="neo4j") as session:
        warm_up(session)

        print(f"{'Depth':6s} | {'Avg (ms)':>9s} | {'Min (ms)':>9s} | {'Max (ms)':>9s} | {'StdDev':>8s} | {'Affected Nodes'}")
        print("-" * 75)

        results = {}
        for depth in DEPTHS:
            latencies = []
            affected_count = None
            for _ in range(N_ITERATIONS):
                ms, count = run_traversal(session, depth)
                latencies.append(ms)
                affected_count = count   # same for every iteration at this depth

            avg = statistics.mean(latencies)
            mn  = min(latencies)
            mx  = max(latencies)
            std = statistics.stdev(latencies)

            results[depth] = (avg, mn, mx, std, affected_count)
            print(f"{depth:<6d} | {avg:9.3f} | {mn:9.3f} | {mx:9.3f} | {std:8.3f} | {affected_count}")

        print("=" * 75)
        print("Plateau analysis (depth vs affected node count):")
        prev_count = None
        for depth in DEPTHS:
            _, _, _, _, count = results[depth]
            if prev_count is not None and count == prev_count:
                print(f"  Depth {depth}: {count} nodes — SAME as depth {depth-1} (graph exhausted)")
            else:
                print(f"  Depth {depth}: {count} nodes — new nodes reached")
            prev_count = count

    driver.close()


if __name__ == "__main__":
    main()