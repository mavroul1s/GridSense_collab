# Cassandra connection module.
# The driver is synchronous, so blocking calls are offloaded to a thread
# pool (execute_async) to avoid freezing the FastAPI event loop.

import os
import asyncio
from functools import partial
from cassandra.cluster import Cluster
from cassandra.policies import DCAwareRoundRobinPolicy
from cassandra.auth import PlainTextAuthProvider
from dotenv import load_dotenv

load_dotenv()

# Initialised once at startup, cleaned up at shutdown.
_cluster: Cluster | None = None
_session = None


def connect() -> None:
    """Open the Cassandra session — host is the Docker service name."""
    global _cluster, _session

    # Optional auth — used only if credentials are set in .env.
    cassandra_user = os.getenv("CASSANDRA_USER")
    cassandra_pass = os.getenv("CASSANDRA_PASSWORD")

    auth_provider = None
    if cassandra_user and cassandra_pass:
        auth_provider = PlainTextAuthProvider(
            username=cassandra_user,
            password=cassandra_pass
        )

    _cluster = Cluster(
        contact_points=["timeseries-db"],
        port=9042,
        auth_provider=auth_provider,
        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc="datacenter1"),
        protocol_version=4,
    )

    _session = _cluster.connect("gridsense")
    print("[Cassandra] Connected to gridsense keyspace on timeseries-db:9042")


def disconnect() -> None:
    global _cluster, _session
    if _cluster:
        _cluster.shutdown()
        print("[Cassandra] Connection closed")
    _cluster = None
    _session = None


def get_session():
    if _session is None:
        raise RuntimeError("Cassandra session not initialised — call connect() first")
    return _session


async def execute_async(query: str, parameters: tuple | list | None = None):
    """Run a CQL statement in a thread pool so the event loop stays free."""
    loop = asyncio.get_event_loop()
    session = get_session()

    if parameters:
        func = partial(session.execute, query, parameters)
    else:
        func = partial(session.execute, query)

    return await loop.run_in_executor(None, func)


def prepare(query: str):
    """Prepare a statement once on the server for repeated execution."""
    session = get_session()
    return session.prepare(query)
