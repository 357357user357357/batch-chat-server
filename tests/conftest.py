"""Shared test configuration.

PyTest imports this module before any test module, and the FastAPI app (with its
SQLAlchemy engine) is created exactly once per run at the first ``import app.main``.
Both test modules point that engine at the same throwaway SQLite file, so deleting
it here guarantees every ``pytest`` run starts from an empty, deterministic database.
"""

import os

_TEST_DB = "/tmp/bc_test_batch.db"
for _suffix in ("", "-wal", "-shm"):
    try:
        os.remove(_TEST_DB + _suffix)
    except FileNotFoundError:
        pass