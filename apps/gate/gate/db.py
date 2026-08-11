import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import env as _env  # noqa: F401  (loads .env before reading it)

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set - the gate cannot start without its database")

# pool.connection() yields a transaction per with-block: commit on clean exit,
# rollback on exception.
pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=1,
    max_size=5,
    kwargs={"row_factory": dict_row},
    open=True,
)
