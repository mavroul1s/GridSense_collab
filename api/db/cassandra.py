# Cassandra connection module.
# The driver is synchronous, so blocking calls run in a thread pool to keep
# the FastAPI event loop free.

import os
import asyncio
from functools import partial
from cassandra.cluster import Cluster
from cassandra.policies import DCAwareRoundRobinPolicy
from cassandra.auth import PlainTextAuthProvider
from dotenv import load_dotenv

load_dotenv()

# Connection state — set once at startup, cleared at shutdown.
_cluster: Cluster | None = None
_session = None


# Open the Cassandra session against the gridsense keyspace.
def connect() -> None:
    """Open the Cassandra session against the gridsense keyspace."""
    global _cluster, _session

    # Read optional credentials from the environment.
    cassandra_user = os.getenv("CASSANDRA_USER")
    cassandra_pass = os.getenv("CASSANDRA_PASSWORD")

    # Build an auth provider only if credentials are configured.
    auth_provider = None
    if cassandra_user and cassandra_pass:
        auth_provider = PlainTextAuthProvider(
            username=cassandra_user,
            password=cassandra_pass
        )

    # Create the cluster handle pointing at the Docker service name.
    _cluster = Cluster(
        contact_points=["timeseries-db"],
        port=9042,
        auth_provider=auth_provider,
        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc="datacenter1"),
        protocol_version=4,
    )

    # Connect and bind the session to the keyspace.
    _session = _cluster.connect("gridsense")
    print("[Cassandra] Connected to gridsense keyspace on timeseries-db:9042")


# Shut down the cluster and clear connection state.
def disconnect() -> None:
    """Shut down the cluster and clear connection state."""
    global _cluster, _session
    if _cluster:
        _cluster.shutdown()
        print("[Cassandra] Connection closed")
    _cluster = None
    _session = None


# Return the active session (raises if connect() was not called).
def get_session():
    """Return the active session, or raise if connect() was not called."""
    if _session is None:
        raise RuntimeError("Cassandra session not initialised — call connect() first")
    return _session


# Run a CQL statement off the event loop (driver is blocking).
async def execute_async(query: str, parameters: tuple | list | None = None):
    """Run a CQL statement in a thread pool so the event loop stays responsive."""
    loop = asyncio.get_event_loop()
    session = get_session()

    # Bind arguments into a no-arg callable for the executor.
    if parameters:
        func = partial(session.execute, query, parameters)
    else:
        func = partial(session.execute, query)

    # Offload the blocking call and await its result.
    return await loop.run_in_executor(None, func)


# Pre-parse a statement on the server for repeated execution.
def prepare(query: str):
    """Pre-parse a statement on the server for repeated execution."""
    session = get_session()
    return session.prepare(query)
