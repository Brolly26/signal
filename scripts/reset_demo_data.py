"""Return the demo database to its seeded state, immediately before recording.

Nothing is deleted unless the exact row ids are named on the command line, and
the deletion plus every verification happen inside ONE transaction that rolls
back if anything diverges from what was expected.

    python scripts/reset_demo_data.py
        Dry run. Lists the journey rows it would offer to remove, with their
        ids, and prints the command to run. Changes nothing.

    python scripts/reset_demo_data.py --id <uuid> [--id <uuid> ...] --confirm
        Removes exactly those rows. Any id that does not exist, or that belongs
        to a seeded request, aborts before the transaction opens.

It never truncates, never drops, never recreates the schema, and never deletes
by a pattern. The dependent rows - votes, analyses, their candidates and
decisions, and any request_links the pair took part in - are removed by the
schema's own ON DELETE CASCADE, not by hand.

Expected end state after a reset, verified inside the transaction:

    8 seeded requests, all of them still present by id
    14 votes
    0 analyses, 0 candidates, 0 decisions, 0 links

If the state after the delete is anything else, the transaction is rolled back
and the database is left exactly as it was.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent
ENV_FILES = (ROOT / "backend" / ".env", ROOT / ".env")

# A request may only be offered for deletion if it carries one of these labels.
# Seeded requests use `demo:<name>` and can never match.
JOURNEY_LABELS = ("demo-run", "audit-journey", "e2e", "P2 check", "P3 check", "P4 check")

SEEDED_LABEL_PREFIX = "demo:"
EXPECTED_SEEDED_REQUESTS = 8
EXPECTED_SEEDED_VOTES = 14


class Divergence(Exception):
    """The state after the delete is not what was expected; roll everything back."""


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


async def candidates(connection) -> list:
    return await connection.fetch(
        """
        SELECT r.id, r.title, r.requester_label,
               (SELECT count(*) FROM request_votes v WHERE v.request_id = r.id) AS votes,
               (SELECT count(*) FROM ai_analyses a WHERE a.request_id = r.id) AS analyses
          FROM feature_requests AS r
         WHERE r.requester_label = ANY($1::text[])
         ORDER BY r.created_at
        """,
        list(JOURNEY_LABELS),
    )


async def snapshot(connection) -> dict:
    return {
        "requests": await connection.fetchval("SELECT count(*) FROM feature_requests"),
        "votes": await connection.fetchval("SELECT count(*) FROM request_votes"),
        "analyses": await connection.fetchval("SELECT count(*) FROM ai_analyses"),
        "candidates": await connection.fetchval(
            "SELECT count(*) FROM analysis_related_requests"
        ),
        "decisions": await connection.fetchval("SELECT count(*) FROM review_decisions"),
        "links": await connection.fetchval("SELECT count(*) FROM request_links"),
    }


async def dry_run(connection) -> int:
    rows = await candidates(connection)
    state = await snapshot(connection)

    print("=" * 74)
    print("Demo data reset - DRY RUN, nothing will change")
    print("=" * 74)
    seeded = await connection.fetchval(
        "SELECT count(*) FROM feature_requests WHERE requester_label LIKE $1",
        SEEDED_LABEL_PREFIX + "%",
    )
    print(f"  requests in the database : {state['requests']}")
    print(f"  seeded (demo:*)          : {seeded}  <- always preserved")
    print()

    if not rows:
        print("  No journey rows found. The database is already at its seeded state.")
        print("=" * 74)
        return 0

    print(f"  {len(rows)} journey row(s) could be removed:")
    for row in rows:
        print(f"      id    {row['id']}")
        print(f"      label {row['requester_label']}  |  {row['title'][:50]}")
        print(f"            {row['votes']} vote(s), {row['analyses']} analysis/analyses "
              f"(removed with it by ON DELETE CASCADE)")
        print()

    flags = " ".join(f"--id {row['id']}" for row in rows)
    print("  To remove them, name them explicitly:")
    print()
    print(f"      python scripts/reset_demo_data.py {flags} --confirm")
    print()
    print("=" * 74)
    return 0


async def perform(connection, requested: list[UUID]) -> int:
    # --- checks that happen BEFORE anything is opened -----------------------
    rows = await connection.fetch(
        "SELECT id, title, requester_label FROM feature_requests WHERE id = ANY($1::uuid[])",
        requested,
    )
    found = {row["id"] for row in rows}

    missing = [str(i) for i in requested if i not in found]
    if missing:
        sys.exit(f"REFUSING: these ids do not exist: {', '.join(missing)}")

    protected = [r for r in rows if r["requester_label"].startswith(SEEDED_LABEL_PREFIX)]
    if protected:
        sys.exit(
            "REFUSING: these ids belong to seeded requests:\n"
            + "\n".join(f"  {r['id']}  {r['title']}" for r in protected)
        )

    not_journey = [r for r in rows if r["requester_label"] not in JOURNEY_LABELS]
    if not_journey:
        sys.exit(
            "REFUSING: these ids are not journey rows:\n"
            + "\n".join(f"  {r['id']}  label={r['requester_label']}" for r in not_journey)
        )

    before = await snapshot(connection)
    seeded_ids_before = {
        row["id"]
        for row in await connection.fetch(
            "SELECT id FROM feature_requests WHERE requester_label LIKE $1",
            SEEDED_LABEL_PREFIX + "%",
        )
    }

    print("=" * 74)
    print("Demo data reset")
    print("=" * 74)
    print(f"  before : {before}")
    for row in rows:
        print(f"  removing {row['id']}  [{row['requester_label']}]  {row['title'][:46]}")

    # --- one transaction: delete, verify, and roll back on any divergence ---
    committed = False
    try:
        async with connection.transaction():
            deleted = await connection.fetch(
                "DELETE FROM feature_requests WHERE id = ANY($1::uuid[]) RETURNING id",
                requested,
            )
            if len(deleted) != len(requested):
                raise Divergence(
                    f"expected to delete {len(requested)} row(s), deleted {len(deleted)}"
                )

            after = await snapshot(connection)

            seeded_ids_after = {
                row["id"]
                for row in await connection.fetch(
                    "SELECT id FROM feature_requests WHERE requester_label LIKE $1",
                    SEEDED_LABEL_PREFIX + "%",
                )
            }
            if seeded_ids_after != seeded_ids_before:
                lost = seeded_ids_before - seeded_ids_after
                raise Divergence(f"{len(lost)} seeded request(s) disappeared: {lost}")

            expectations = {
                "requests": EXPECTED_SEEDED_REQUESTS,
                "votes": EXPECTED_SEEDED_VOTES,
                "analyses": 0,
                "candidates": 0,
                "decisions": 0,
                "links": 0,
            }
            wrong = {k: (after[k], v) for k, v in expectations.items() if after[k] != v}
            if wrong:
                raise Divergence(
                    "end state does not match: "
                    + ", ".join(f"{k} is {got}, expected {want}" for k, (got, want) in wrong.items())
                )

            # Everything verified inside the transaction; leaving the block commits.
            committed = True
            verified = after
    except Divergence as error:
        print()
        print(f"  ROLLED BACK: {error}")
        print("  The database is exactly as it was. Nothing was removed.")
        print("=" * 74)
        return 1

    print()
    print(f"  removed {len(requested)} request(s), verified inside the transaction, committed")
    print(f"  after  : {verified}")
    print(f"  all {len(seeded_ids_before)} seeded requests still present by id")
    print("=" * 74)
    return 0 if committed else 1


async def main(arguments) -> int:
    import asyncpg

    connection = await asyncpg.connect(dsn=load_database_url(), statement_cache_size=0)
    try:
        if not arguments.confirm:
            if arguments.id:
                print("  --id given without --confirm; showing the dry run instead.\n")
            return await dry_run(connection)

        if not arguments.id:
            sys.exit(
                "REFUSING: --confirm requires at least one --id. Run without arguments "
                "to see the candidates and the exact command."
            )
        return await perform(connection, arguments.id)
    finally:
        await connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--id", type=UUID, action="append", default=[],
        help="A request id to remove. Repeat for several. Required with --confirm.",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="Actually perform the deletion. Without it, this is a dry run.",
    )
    raise SystemExit(asyncio.run(main(parser.parse_args())))
