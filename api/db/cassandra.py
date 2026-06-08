# api/db/cassandra.py
# Cassandra connection module for GridSense API
#
# cassandra-driver is synchronous — it uses its own thread pool internally.
# Running blocking Cassandra calls directly in an async FastAPI endpoint
# would freeze the event loop and block all other requests.
#
# Solution: run_in_executor() offloads the blocking call to a ThreadPoolExecutor.
# FastAPI awaits the result without blocking the event loop.
# This pattern is the standard approach when a driver has no native async support.

import os
import asyncio
from functools import partial
from cassandra.cluster import Cluster
from cassandra.policies import DCAwareRoundRobinPolicy
from cassandra.auth import PlainTextAuthProvider
from dotenv import load_dotenv

load_dotenv()

# ── CONNECTION STATE ──────────────────────────────────────────────
# Module-level variables — initialised once at API startup via connect()
# and cleaned up at shutdown via disconnect()
_cluster: Cluster | None = None
_session = None


# ── CONNECT ───────────────────────────────────────────────────────
def connect() -> None:
    """
    Establish a synchronous Cassandra connection at application startup.
    Called once from main.py lifespan context manager.

    Host: 'timeseries-db' — Docker service name (Trap 10 fix).
    Week 4 Slide 16: containers discover each other by service name,
    never by localhost or container name.
    """
    global _cluster, _session

    # Optional auth — reads from .env if set, skips if not configured
    cassandra_user = os.getenv("CASSANDRA_USER")
    cassandra_pass = os.getenv("CASSANDRA_PASSWORD")

    auth_provider = None
    if cassandra_user and cassandra_pass:
        auth_provider = PlainTextAuthProvider(
            username=cassandra_user,
            password=cassandra_pass
        )

    _cluster = Cluster(
        contact_points=["timeseries-db"],   # Docker service name — NOT localhost
        port=9042,
        auth_provider=auth_provider,
        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc="datacenter1"),
        protocol_version=4,
    )

    _session = _cluster.connect("gridsense")  # keyspace from cql/init.cql
    print("[Cassandra] Connected to gridsense keyspace on timeseries-db:9042")


# ── DISCONNECT ────────────────────────────────────────────────────
def disconnect() -> None:
    """
    Cleanly shut down the Cassandra connection at application shutdown.
    Called from main.py lifespan context manager on exit.
    """
    global _cluster, _session
    if _cluster:
        _cluster.shutdown()
        print("[Cassandra] Connection closed")
    _cluster = None
    _session = None


# ── SESSION ACCESSOR ─────────────────────────────────────────────
def get_session():
    """
    Returns the active Cassandra session.
    Raises RuntimeError if connect() has not been called.
    """
    if _session is None:
        raise RuntimeError("Cassandra session not initialised — call connect() first")
    return _session


# ── ASYNC EXECUTE ─────────────────────────────────────────────────
async def execute_async(query: str, parameters: tuple | list | None = None):
    """
    Run a Cassandra CQL statement from an async context without blocking
    the FastAPI event loop.

    cassandra-driver's session.execute() is synchronous — calling it
    directly inside an async def would block the entire event loop until
    Cassandra responds, preventing other requests from being served.

    run_in_executor() submits the blocking call to a ThreadPoolExecutor
    (the default executor used by asyncio). FastAPI awaits the Future
    without blocking — other requests continue to be served while
    Cassandra processes the query.

    Args:
        query      : CQL string or PreparedStatement
        parameters : positional parameters tuple/list, or None

    Returns:
        cassandra.cluster.ResultSet
    """
    loop = asyncio.get_event_loop()
    session = get_session()

    if parameters:
        # partial() binds the arguments so run_in_executor can call
        # the function with no arguments (it only accepts callables)
        func = partial(session.execute, query, parameters)
    else:
        func = partial(session.execute, query)

    return await loop.run_in_executor(None, func)


# ── PREPARED STATEMENT HELPER ─────────────────────────────────────
def prepare(query: str):
    """
    Prepare a CQL statement for repeated execution.
    Prepared statements are parsed once on the server — subsequent
    executions send only parameter values, reducing per-query overhead.
    Returns a PreparedStatement that can be passed to execute_async().
    """
    session = get_session()
    return session.prepare(query)