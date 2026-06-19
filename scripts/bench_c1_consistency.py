# scripts/bench_c1_consistency.py
# Part C.1 — Cassandra write throughput vs consistency level
#
# Methodology v2: controls for ordering/warm-up bias.
#   1. A warm-up pass (discarded) primes the connection, prepared
#      statement cache, and Cassandra's own page cache.
#   2. Each consistency level is run 3 times in RANDOMIZED order,
#      and results are averaged — this cancels out any advantage
#      gained purely from running later in the sequence.
#
# Cluster note: SimpleStrategy RF=1 (single-node dev, Week 6 Slide 48).
# With one replica, ONE/LOCAL_QUORUM/ALL all contact the same single
# node — any throughput difference should be near zero. This is the
# expected and reportable finding for a dev-scale cluster.

import time
import random
from cassandra.cluster import Cluster
from cassandra.policies import DCAwareRoundRobinPolicy
from cassandra import ConsistencyLevel

KEYSPACE   = "gridsense"
N_WRITES   = 3000   # writes per trial
N_TRIALS   = 3       # trials per consistency level, randomized order


def setup(session):
    session.execute("""
        CREATE TABLE IF NOT EXISTS bench_writes (
            run_id TEXT,
            seq INT,
            value FLOAT,
            PRIMARY KEY (run_id, seq)
        )
    """)
    session.execute("TRUNCATE bench_writes")


def warm_up(session, n=1000):
    """Discard-result warm-up pass — primes driver, prepared statement
    cache, and Cassandra page cache before real measurements begin."""
    insert = session.prepare(
        "INSERT INTO bench_writes (run_id, seq, value) VALUES (?, ?, ?)"
    )
    insert.consistency_level = ConsistencyLevel.ONE
    for i in range(n):
        session.execute(insert, ("WARMUP", i, float(i)))
    print(f"Warm-up complete ({n} discarded writes)\n")


def run_trial(session, cl_name, cl_value, trial_num, n=N_WRITES):
    insert = session.prepare(
        "INSERT INTO bench_writes (run_id, seq, value) VALUES (?, ?, ?)"
    )
    insert.consistency_level = cl_value
    run_id = f"{cl_name}_{trial_num}"

    start = time.perf_counter()
    for i in range(n):
        session.execute(insert, (run_id, i, float(i)))
    elapsed = time.perf_counter() - start

    throughput = n / elapsed
    return elapsed, throughput


def main():
    cluster = Cluster(
        contact_points=["timeseries-db"],
        port=9042,
        protocol_version=4,
        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc="datacenter1"),
    )
    session = cluster.connect(KEYSPACE)
    setup(session)

    print("=" * 70)
    print("C.1 — Cassandra Write Throughput vs Consistency Level (v2)")
    print(f"Cluster: SimpleStrategy, RF=1 (single-node dev)")
    print(f"Methodology: warm-up pass + {N_TRIALS} randomized-order trials/CL")
    print("=" * 70)

    warm_up(session)

    # Build randomized trial schedule: e.g.
    # [(ALL,1), (ONE,1), (LOCAL_QUORUM,1), (ONE,2), (ALL,2), ...]
    levels = [
        ("ONE",          ConsistencyLevel.ONE),
        ("LOCAL_QUORUM",  ConsistencyLevel.LOCAL_QUORUM),
        ("ALL",           ConsistencyLevel.ALL),
    ]
    schedule = []
    for trial_num in range(1, N_TRIALS + 1):
        round_levels = levels.copy()
        random.shuffle(round_levels)
        for cl_name, cl_value in round_levels:
            schedule.append((cl_name, cl_value, trial_num))

    print(f"{'Trial':6s} | {'Consistency Level':17s} | {'Time':>7s}  | {'Throughput'}")
    print("-" * 70)

    raw_results = {name: [] for name, _ in levels}

    for cl_name, cl_value, trial_num in schedule:
        elapsed, throughput = run_trial(session, cl_name, cl_value, trial_num)
        raw_results[cl_name].append(throughput)
        print(f"{trial_num:<6d} | {cl_name:17s} | {elapsed:7.2f}s | {throughput:9.1f} writes/sec")

    print("=" * 70)
    print(f"{'Consistency Level':17s} | {'Avg Throughput':>15s} | {'Std Dev'}")
    print("-" * 70)

    avgs = {}
    for cl_name in raw_results:
        vals = raw_results[cl_name]
        avg = sum(vals) / len(vals)
        std = (sum((v - avg) ** 2 for v in vals) / len(vals)) ** 0.5
        avgs[cl_name] = avg
        print(f"{cl_name:17s} | {avg:13.1f} w/s | ±{std:.1f}")

    print("=" * 70)
    one_avg = avgs["ONE"]
    for cl_name, avg in avgs.items():
        delta_pct = ((avg - one_avg) / one_avg) * 100
        print(f"{cl_name:17s}: {avg:9.1f} writes/sec avg  ({delta_pct:+.1f}% vs ONE)")

    cluster.shutdown()


if __name__ == "__main__":
    main()