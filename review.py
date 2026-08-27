#!/usr/bin/env python3
"""claude-review entry point: collect -> analyze -> report -> open browser.

python review.py            # yesterday's sessions (mtime), open report
python review.py --all      # every session
python review.py --judge    # also run the Claude API blame judgment
python review.py --no-open  # don't open the browser
"""
import argparse
import sys
import io
import os
import webbrowser
from datetime import datetime, timedelta

import db
from collectors import claude_code, config_snapshot
from analyzers import detect, phrases, judge
from report import build as report_build

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT_DIR = os.path.join(os.path.dirname(__file__), "out")


def collect(conn, since):
    n_sessions = n_msgs = 0
    project = None
    for session, msgs in claude_code.iter_sessions(since=since):
        db.upsert_session(conn, session)
        db.replace_messages(conn, session["id"], msgs)
        project = project or session.get("project")
        n_sessions += 1
        n_msgs += len(msgs)
    # config snapshot for the judge, stamped onto every session
    cfg_id = config_snapshot.snapshot(conn, project)
    config_snapshot.stamp_sessions(conn, cfg_id)
    conn.commit()
    return n_sessions, n_msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--all", action="store_true", help="parse every session (ignore mtime)"
    )
    ap.add_argument("--since", help="only files modified since YYYY-MM-DD")
    ap.add_argument(
        "--judge", action="store_true", help="run the Claude API blame judgment"
    )
    ap.add_argument(
        "--no-open", action="store_true", help="don't open the report in a browser"
    )
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

    n_inc = detect.detect_all(conn)
    n_ph = phrases.compute(conn)
    n_rounds = conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0]
    print(f"detected {n_inc} incidents, {n_rounds} rounds; {n_ph} phrase tallies")

    if args.judge:
        judged, msg = judge.judge_all(conn)
        print(f"judge: {msg}")

    path = report_build.build(conn, OUT_DIR)
    print(f"report: {path}")
    if not args.no_open:
        webbrowser.open("file://" + os.path.abspath(path))


if __name__ == "__main__":
    main()
