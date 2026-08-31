#!/usr/bin/env python3
"""
Export all conversations from a batch_chat SQLite DB to a JSON file
that can be re-imported via the /api/import endpoint.

Usage:
    python3 scripts/export_db.py /path/to/data/batch_chat.db [output.json]
"""
import json
import sqlite3
import sys


def export(db_path: str, output: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    conversations = []
    for conv in cur.execute("SELECT id, title FROM conversations").fetchall():
        messages = []
        for msg in cur.execute(
            "SELECT role, content, model FROM messages WHERE conversation_id = ? ORDER BY id",
            (conv["id"],),
        ).fetchall():
            messages.append(
                {
                    "role": msg["role"],
                    "content": msg["content"],
                    **({"model": msg["model"]} if msg["model"] else {}),
                }
            )
        conversations.append({"title": conv["title"], "messages": messages})

    conn.close()

    with open(output, "w", encoding="utf-8") as fh:
        json.dump({"conversations": conversations}, fh, ensure_ascii=False, indent=2)
    print(f"Exported {len(conversations)} conversations to {output}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    db_path = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else "batch_chat_export.json"
    export(db_path, output)