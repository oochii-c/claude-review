"""Frequently-used phrases (SPEC 5.5).

User side is rule-countable now: tally the correction markers detect.py already
recognizes, bucketed by ISO week. Model side and synonym normalization need an
API pass and are left for judge-time; this fills the user column only.
"""

from collections import defaultdict
from datetime import datetime
import json

from . import detect


def _week(ts):
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return "unknown"
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def compute(conn):
    """Recompute user-side phrase tallies. Idempotent."""
    conn.execute("DELETE FROM phrases WHERE side='user'")
    # week -> canonical -> [count, examples]
    tally = defaultdict(lambda: defaultdict(lambda: [0, []]))
    for m in conn.execute(
        "SELECT ts, text FROM messages WHERE role='user' AND text IS NOT NULL"
    ):
        hit = detect._redirect_hit(m["text"])
        if not hit:
            continue
        canon = hit[0]
        wk = _week(m["ts"])
        cell = tally[wk][canon]
        cell[0] += 1
        if len(cell[1]) < 3:
            cell[1].append(m["text"][:80])
    n = 0
    for wk, canons in tally.items():
        for canon, (count, examples) in canons.items():
            conn.execute(
                """INSERT INTO phrases(side, canonical, count, week, examples_json)
                   VALUES('user', ?, ?, ?, ?)""",
                (canon, count, wk, json.dumps(examples, ensure_ascii=False)),
            )
            n += 1
    conn.commit()
    return n
