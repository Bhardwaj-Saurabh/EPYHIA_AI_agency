"""Minimal forward-only migration runner: applies db/migrations/*.sql in
filename order, tracking applied files in schema_migrations."""

from .db import pool
from .env import REPO_ROOT

MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"


def migrate() -> None:
    with pool.connection() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                 name TEXT PRIMARY KEY,
                 applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )"""
        )
        applied = {
            r["name"] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()
        }

    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        with pool.connection() as conn:  # one transaction per migration
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
        print(f"applied {path.name}")
    print("migrations up to date")


if __name__ == "__main__":
    migrate()
