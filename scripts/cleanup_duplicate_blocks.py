#!/usr/bin/env python3
"""One-time cleanup for the duplicate-message flood (found Sep 2026).

A buggy client release synced whole dialog lists with the same dialog
appended over and over, so the master DB stored the identical Q/A block
dozens of times inside one conversation (32 copies of one pair in convs
36/61 on the production server). The ingest paths now collapse such
floods (app.services.phone_sync.collapse_repeated_blocks); this script
cleans the ALREADY STORED rows.

For every live conversation whose message list is a (possibly empty)
prefix followed by the same block repeated >= --min-repeats times, it
keeps one copy and tombstones the rest (deleted_at + a message_tombstones
row, exactly like the web UI's per-message delete), so stale device
pushes can never resurrect the copies. Conversations are stamped with a
new updated_at so every device pulls the cleaned state.

Self-contained (stdlib only) — runs on the host, inside the container,
or anywhere the DB file is mounted:

    python3 scripts/cleanup_duplicate_blocks.py --db data/batch_chat.db --dry-run
    python3 scripts/cleanup_duplicate_blocks.py --db data/batch_chat.db
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone

DELETED_BY = "dup-block-cleanup"


def collapse_point(ids: list[tuple[str | None, str | None]], min_repeats: int) -> int | None:
    """Index where the periodic flood tail starts (None = nothing to clean).

    Same algorithm as app.services.phone_sync.collapse_repeated_blocks —
    kept in sync deliberately so the script matches the server behavior.
    """
    n = len(ids)
    for offset in range(0, n - min_repeats + 1):
        tail = n - offset
        for block in range(1, tail // min_repeats + 1):
            if tail % block:
                continue
            head = ids[offset:offset + block]
            if all(ids[i] == head[(i - offset) % block] for i in range(offset, n)):
                return offset + block
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="path to batch_chat.db")
    parser.add_argument("--min-repeats", type=int, default=3,
                        help="collapse a tail only when the block repeats at "
                             "least this many times (default: 3)")
    parser.add_argument("--account", help="only clean this account_id")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be cleaned, change nothing")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")

    sql = ("SELECT id, external_id, kind, title FROM conversations "
           "WHERE deleted_at IS NULL")
    params: list = []
    if args.account:
        sql += " AND account_id = ?"
        params.append(args.account)
    conversations = conn.execute(sql, params).fetchall()

    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    cleaned_convs = 0
    tombstoned = 0
    for conv in conversations:
        rows = conn.execute(
            "SELECT id, role, content FROM messages "
            "WHERE conversation_id = ? AND deleted_at IS NULL "
            "ORDER BY (sort_index IS NULL), sort_index, id",
            (conv["id"],),
        ).fetchall()
        ids = [(r["role"], r["content"]) for r in rows]
        keep = collapse_point(ids, args.min_repeats)
        if keep is None:
            continue
        extras = rows[keep:]
        reps = (len(rows) - keep) // max(keep, 1)
        print(
            f"conversation {conv['id']} ({conv['kind']} "
            f"\"{conv['title'][:48]}\", external_id={conv['external_id']}): "
            f"keeping {keep}/{len(rows)} live messages, tombstoning "
            f"{len(extras)} flood copies (block x{reps})"
        )
        cleaned_convs += 1
        if args.dry_run:
            continue
        for row in extras:
            conn.execute(
                "UPDATE messages SET deleted_at = ?, deleted_by = ? WHERE id = ?",
                (now, DELETED_BY, row["id"]),
            )
            conn.execute(
                "INSERT INTO message_tombstones "
                "(conversation_id, role, content, deleted_at, deleted_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (conv["id"], row["role"], row["content"], now, DELETED_BY),
            )
            tombstoned += 1
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conv["id"]),
        )

    if not args.dry_run:
        conn.commit()
    conn.close()

    mode = "would clean" if args.dry_run else "cleaned"
    print(f"{mode} {cleaned_convs} conversation(s), "
          f"{'would tombstone' if args.dry_run else 'tombstoned'} {tombstoned} message(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())