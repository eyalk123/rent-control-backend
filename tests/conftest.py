"""Shared test fixtures: in-memory SQLite engine, session isolation, and an
authenticated FastAPI TestClient.

The whole suite runs against a single in-memory SQLite database (StaticPool keeps
one underlying connection alive). Each test runs inside an outer transaction that
is rolled back on teardown, so tests never see each other's data.

Two production seams are swapped via FastAPI dependency overrides:
  * ``get_db``           -> the per-test session
  * ``get_current_user`` -> a fixed owner dict (no Firebase network call)

One real SQLite gap is patched here: ``transaction_repository.get_monthly_summary``
calls ``func.extract('year'|'month', date)``, which SQLite lacks. We register an
``extract`` SQLite function on connect. (The report service uses SQLAlchemy's native
``extract()`` construct, which the SQLite dialect already compiles to strftime.)
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Import every model module so all tables (incl. association tables
# transaction_categories / supplier_categories) register on Base.metadata.
import app.models  # noqa: F401
from app.api.dependencies import get_current_user
from app.database import get_db
from app.main import app
from app.models.base import Base

OWNER_A = "owner-a"
OWNER_B = "owner-b"


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _sqlite_extract(field, value):
    """Stand-in for SQL ``extract(field FROM date)`` on SQLite.

    SQLite stores DATE columns as ISO strings ('YYYY-MM-DD'); pull the requested
    component out of the leading 10 characters.
    """
    if value is None:
        return None
    parts = str(value)[:10].split("-")
    if field == "year":
        return int(parts[0])
    if field == "month":
        return int(parts[1])
    if field == "day":
        return int(parts[2])
    raise ValueError(f"Unsupported extract field: {field}")


@event.listens_for(engine, "connect")
def _on_connect(dbapi_conn, _record):
    # Disable pysqlite's implicit transaction handling so SQLAlchemy controls
    # BEGIN itself — required for the savepoint-based per-test rollback to work.
    dbapi_conn.isolation_level = None
    # Make ON DELETE SET NULL / CASCADE behave like Postgres.
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    # Provide the missing extract() scalar function used by get_monthly_summary.
    dbapi_conn.create_function("extract", 2, _sqlite_extract)


@event.listens_for(engine, "begin")
def _on_begin(conn):
    # Pair with isolation_level=None above: emit a real BEGIN per transaction.
    conn.exec_driver_sql("BEGIN")


Base.metadata.create_all(bind=engine)


@pytest.fixture
def db_session():
    """A session wrapped in an outer transaction that is rolled back after the test.

    ``join_transaction_mode="create_savepoint"`` lets the app code call
    ``session.commit()`` freely (each commit releases a SAVEPOINT) while the outer
    transaction still discards everything on rollback.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(
        bind=connection, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client_factory(db_session):
    """Returns a builder for a TestClient authenticated as a given owner.

    All clients share the single ``db_session`` so data seeded directly is visible
    through the API. Because dependency overrides are global, build/use one owner's
    client before moving to the next (requests are synchronous, so the override in
    effect at request time is what matters).
    """
    def _make(owner_id: str = OWNER_A) -> TestClient:
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": owner_id,
            "role": "owner",
        }
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_factory):
    """Convenience client authenticated as OWNER_A."""
    return client_factory(OWNER_A)
