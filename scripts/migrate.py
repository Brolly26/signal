"""Apply versioned SQL migrations.

Deliberately small: there is no ORM in this project, so there is no Alembic
either. Migrations are plain SQL files applied in filename order and recorded in
a `schema_migrations` table, with a checksum so an already-applied file cannot
be edited silently.

Usage:
    python scripts/migrate.py            # apply pending migrations
    python scripts/migrate.py --status   # show what is applied
"""

import asyncio
import hashlib
import os
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
ENV_FILES = (ROOT / "backend" / ".env", ROOT / ".env")

CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def load_database_url() -> str:
    """Read DATABASE_URL from the environment, falling back to a .env file.

    A five-line parser avoids adding python-dotenv for one variable.
    """
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    for env_file in ENV_FILES:
        if not env_file.exists():
            continue
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "DATABASE_URL":
                return value.strip().strip('"').strip("'")

    sys.exit(
        "DATABASE_URL is not set. Copy .env.example to backend/.env and fill it in, "
        "or export DATABASE_URL."
    )


def discover_migrations() -> list[tuple[str, str, str]]:
    """Return (version, checksum, sql) for every migration, in filename order."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        sys.exit(f"No migrations found in {MIGRATIONS_DIR}")

    migrations = []
    for path in files:
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]
        migrations.append((path.stem, checksum, sql))
    return migrations


async def run(show_status_only: bool) -> None:
    database_url = load_database_url()
    migrations = discover_migrations()

    connection = await asyncpg.connect(dsn=database_url, statement_cache_size=0)
    try:
        await connection.execute(CREATE_TRACKING_TABLE)
        applied = {
            row["version"]: row["checksum"]
            for row in await connection.fetch("SELECT version, checksum FROM schema_migrations")
        }

        if show_status_only:
            for version, checksum, _ in migrations:
                state = "applied" if version in applied else "pending"
                print(f"  {version}  {state}  (checksum {checksum})")
            return

        pending = 0
        for version, checksum, sql in migrations:
            if version in applied:
                if applied[version] != checksum:
                    sys.exit(
                        f"Migration {version} was already applied but its file changed "
                        f"(recorded {applied[version]}, now {checksum}). "
                        "Add a new migration instead of editing an applied one."
                    )
                continue

            # Each migration runs in its own transaction: a failure leaves the
            # schema untouched rather than half-applied.
            async with connection.transaction():
                await connection.execute(sql)
                await connection.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                    version,
                    checksum,
                )
            print(f"  applied {version}")
            pending += 1

        print(f"Done. {pending} migration(s) applied, {len(applied)} already present.")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(run(show_status_only="--status" in sys.argv))
