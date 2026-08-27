#!/usr/bin/env python3
"""claude-review entry point: collect -> analyze -> report -> open browser.

Currently implemented: collect (Claude Code JSONL -> review.db).
"""
import argparse
import sys
import io
from datetime import datetime, timedelta

import db
from collectors import claude_code

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def collect(conn, since):
    n_sessions = n_msgs = 0
    for session, msgs in claude_code.iter_sessions(since=since):
        db.upsert_session(conn, session)
        db.replace_messages(conn, session["id"], msgs)
        n_sessions += 1
        n_msgs += len(msgs)
    conn.commit()
    return n_sessions, n_msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--all", action="store_true", help="parse every session (ignore mtime)"
    )
    ap.add_argument("--since", help="only files modified since YYYY-MM-DD")
    args = ap.parse_args()

    if args.all:
        since = None
    elif args.since:
        since = datetime.fromisoformat(args.since)
    else:
        since = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=1)

    conn = db.connect()
    ns, nm = collect(conn, since)
    scope = "all" if since is None else f"since {since.date()}"
    print(f"collected {ns} sessions, {nm} messages ({scope})")

    # quick sanity readout
    row = conn.execute(
        "SELECT COUNT(*) c, COUNT(DISTINCT session_id) s FROM messages"
    ).fetchone()
    print(f"db totals: {row['c']} messages across {row['s']} sessions")


if __name__ == "__main__":
    main()
