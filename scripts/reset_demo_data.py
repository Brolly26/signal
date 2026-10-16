"""Return the demo database to its seeded state, immediately before recording.

It removes ONLY requests created by a test journey, identified explicitly by
their `requester_label`. It never truncates, never drops, never recreates the
schema, and never touches a row the seed created.

    python scripts/reset_demo_data.py                 # dry run: shows the plan
    python scripts/reset_demo_data.py --confirm       # performs the deletion

A dry run is the default on purpose: the only way to delete something is to ask
for it in so many words.

What is removed, and nothing else:

  * feature_requests whose requester_label is in JOURNEY_LABELS
  * the rows that depend on them, which the schema's ON DELETE CASCADE removes:
    their votes, their analyses, those analyses' candidates and decisions, and
    any request_links the pair took part in

What is preserved:

  * every request whose label starts with `demo:` (the seed) and its votes
  * the schema, its constraints and its indexes
  * schema_migrations

The script refuses to run if the deletion would touch a seeded request, and it
verifies the expected end state afterwards instead of assuming it.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILES = (ROOT / "backend" / ".env", ROOT / ".env")

# Labels used by a test or verification journey. A request is only ever removed
# if it carries one of these; anything else is left alone.
JOURNEY_LABELS = ("demo-run", "audit-journey", "e2e", "P2 check", "P3 check", "P4 check")

EXPECTED_SEEDED_REQUESTS = 8
EXPECTED_SEEDED_VOTES = 14


def load_database_url() -> str:
    """Read DATABASE_URL from the environment or a .env file.

    The value is used, never printed: a connection string carries a password.
    """
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    for env_file in ENV_FILES:
        if not env_file.exists():
            continue
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("DATABASE_URL=") and "=" in line:
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value

    sys.exit("DATABASE_URL is not set.")


async def main(confirm: bool) -> int:
    import asyncpg

    connection = await asyncpg.connect(dsn=load_database_url(), statement_cache_size=0)
    try:
        doomed = await connection.fetch(
            """
            SELECT id, title, requester_label,
                   (SELECT count(*) FROM request_votes v WHERE v.request_id = r.id) AS votes,
                   (SELECT count(*) FROM ai_analyses a WHERE a.request_id = r.id) AS analyses
              FROM feature_requests AS r
             WHERE r.requester_label = ANY($1::text[])
             ORDER BY r.created_at
            """,
            list(JOURNEY_LABELS),
        )

        seeded = await connection.fetchval(
            "SELECT count(*) FROM feature_requests WHERE requester_label LIKE 'demo:%'"
        )
        total = await connection.fetchval("SELECT count(*) FROM feature_requests")

        print("=" * 70)
        print("Demo data reset" + ("" if confirm else "  (DRY RUN - nothing will change)"))
        print("=" * 70)
        print(f"  requests in the database : {total}")
        print(f"  seeded (demo:*)          : {seeded}  <- preserved")
        print(f"  journey rows to remove   : {len(doomed)}")
        for row in doomed:
            print(f"      - [{row['requester_label']}] {row['title'][:46]}")
            print(f"        {row['votes']} vote(s), {row['analyses']} analysis/analyses "
                  f"(removed with it by ON DELETE CASCADE)")

        if not doomed:
            print("\n  Nothing to remove; the database is already at its seeded state.")
            return 0

        # A journey label can never belong to the seed, but the check is cheap
        # and this script deletes rows.
        if any(row["requester_label"].startswith("demo:") for row in doomed):
            sys.exit("REFUSING: the selection includes a seeded request.")

        if not confirm:
            print("\n  Dry run. Re-run with --confirm to perform the deletion.")
            return 0

        async with connection.transaction():
            removed = await connection.fetch(
                """
                DELETE FROM feature_requests
                 WHERE requester_label = ANY($1::text[])
                RETURNING id
                """,
                list(JOURNEY_LABELS),
            )

        print(f"\n  removed {len(removed)} request(s)")

        # Verify the end state rather than assume it.
        after = {
            "requests": await connection.fetchval("SELECT count(*) FROM feature_requests"),
            "votes": await connection.fetchval("SELECT count(*) FROM request_votes"),
            "analyses": await connection.fetchval("SELECT count(*) FROM ai_analyses"),
            "candidates": await connection.fetchval(
                "SELECT count(*) FROM analysis_related_requests"
            ),
            "decisions": await connection.fetchval("SELECT count(*) FROM review_decisions"),
            "links": await connection.fetchval("SELECT count(*) FROM request_links"),
        }
        print("\n  final state:")
        for key, value in after.items():
            print(f"      {key:12s} {value}")

        ok = (
            after["requests"] == EXPECTED_SEEDED_REQUESTS
            and after["votes"] == EXPECTED_SEEDED_VOTES
            and after["analyses"] == 0
            and after["decisions"] == 0
            and after["links"] == 0
        )
        print()
        print("  " + ("back to the seeded state" if ok else "UNEXPECTED STATE - check above"))
        print("=" * 70)
        return 0 if ok else 1
    finally:
        await connection.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(confirm="--confirm" in sys.argv)))
