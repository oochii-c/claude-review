"""Rule-based incident / round candidate detection (SPEC 5.1, 5.3).

Cuts candidate spans; does NOT assign blame or outcome — that is judge.py's job
(a separate API call). Correction lexicon is deliberately interpersonal-redirect
only (아니/다시/말했잖아/왜자꾸), kept apart from error-domain words (에러/안돼)
to avoid the vocabulary confound where a debugging thread's own vocabulary reads
as a correction. See reasoning: bug-domain words inflate false corrections.
"""
import re
import json

# --- correction lexicon, normalized to a canonical marker + strength (0-10) ---
# strength feeds the rule-based heat candidate; API refines tone later (5.4).
REDIRECT = [
    # (regex, canonical, strength)
    (re.compile(r"왜 ?자꾸|왜 ?계속"), "왜자꾸", 9),
    (re.compile(r"말했?잖|얘기했?잖|시켰?잖|랬잖"), "말했잖아", 8),
    (re.compile(r"몇 ?번(을|이나| 말)"), "몇번을", 8),
    (re.compile(r"하지 ?마|하지 ?말|넣지 ?마|건들지 ?마|건드리지 ?마"), "하지마", 7),
    (re.compile(r"그게 ?아니|그거 ?말고|그거 ?아니|이거 ?말고|저거 ?말고"), "그게아니라", 6),
    (re.compile(r"^(아니|아냐)([ ,.]|$)"), "아니", 4),
    (re.compile(r"다시"), "다시", 4),
    (re.compile(r"말고\b|말고$"), "말고", 4),
    (re.compile(r"아까"), "아까", 3),
]
CURSE = re.compile(r"ㅅㅂ|시발|씨발|짜증|답답|에휴|아 진짜|아진짜")
BANG = re.compile(r"[!?]{2,}|[!]{1,}$")

# cluster window: correction user-turns within this many messages join one incident
CLUSTER_GAP = 6


def _redirect_hit(text):
    """Return (canonical, strength) for the strongest redirect marker, or None."""
    best = None
    for rx, canon, strength in REDIRECT:
        if rx.search(text):
            if best is None or strength > best[1]:
                best = (canon, strength)
    return best


def _heat(text, base_strength):
    h = base_strength
    if CURSE.search(text):
        h = max(h, 9)
    if BANG.search(text):
        h = min(10, h + 1)
    return min(10, h)


def load_session(conn, sid):
    return conn.execute(
        "SELECT idx, role, ts, text, tool_calls_json FROM messages "
        "WHERE session_id=? ORDER BY idx",
        (sid,),
    ).fetchall()


def _file_churn(msgs):
    """Flag idx ranges where the same file is edited >=3 times — silent struggle
    even without a user correction (SPEC 5.1: same file repeated short-term)."""
    spans = []
    hits = {}  # file -> list of idx
    for m in msgs:
        if m["role"] != "assistant" or not m["tool_calls_json"]:
            continue
        try:
            tools = json.loads(m["tool_calls_json"])
        except Exception:
            continue
        for t in tools:
            if t.get("name") in ("Edit", "Write", "NotebookEdit"):
                try:
                    fp = json.loads(t.get("input", "{}")).get("file_path")
                except Exception:
                    fp = None
                if fp:
                    hits.setdefault(fp, []).append(m["idx"])
    for fp, idxs in hits.items():
        if len(idxs) >= 3 and (idxs[-1] - idxs[0]) <= 12:
            spans.append((idxs[0], idxs[-1]))
    return spans


def find_incidents(msgs):
    """Return list of incident-candidate dicts (no blame/outcome yet)."""
    # 1) collect user redirect turns.
    # A correction can't be the session's first user turn (nothing to correct
    # yet) — that catches initial instructions like "변경하지 마세요".
    first_user_idx = next((m["idx"] for m in msgs if m["role"] == "user"), None)
    marks = []  # (idx, canonical, strength, heat, text)
    for m in msgs:
        if m["role"] != "user" or not m["text"] or m["idx"] == first_user_idx:
            continue
        hit = _redirect_hit(m["text"])
        if hit:
            canon, strength = hit
            marks.append((m["idx"], canon, strength, _heat(m["text"], strength), m["text"]))

    incidents = []
    if marks:
        # 2) cluster marks that are within CLUSTER_GAP of each other
        cluster = [marks[0]]
        for mk in marks[1:]:
            if mk[0] - cluster[-1][0] <= CLUSTER_GAP:
                cluster.append(mk)
            else:
                incidents.append(_mk_incident(msgs, cluster))
                cluster = [mk]
        incidents.append(_mk_incident(msgs, cluster))

    # 3) add file-churn incidents that don't overlap an existing span
    covered = [(i["start_idx"], i["end_idx"]) for i in incidents]
    for a, b in _file_churn(msgs):
        if any(not (b < s or a > e) for s, e in covered):
            continue
        incidents.append({
            "start_idx": a, "end_idx": b, "wasted_turns": b - a + 1,
            "heat": 3, "signals": json.dumps(["file_churn"], ensure_ascii=False),
            "n_redirects": 0,
        })
    incidents.sort(key=lambda x: x["start_idx"])
    return incidents


def _mk_incident(msgs, cluster):
    # span starts at the request that preceded the first correction (one user
    # turn back if available), ends at the last correction's index.
    first_idx = cluster[0][0]
    # span starts at the nearest preceding user turn (the request that went wrong)
    prev_user = [m["idx"] for m in msgs if m["role"] == "user" and m["idx"] < first_idx]
    start = prev_user[-1] if prev_user else first_idx
    end = cluster[-1][0]
    return {
        "start_idx": start,
        "end_idx": end,
        "wasted_turns": end - start + 1,
        "heat": max(c[3] for c in cluster),
        "signals": json.dumps(sorted({c[1] for c in cluster}), ensure_ascii=False),
        "n_redirects": len(cluster),
    }


def detect_all(conn):
    """Recompute incidents for every session. Idempotent (wipes prior rows)."""
    conn.execute("DELETE FROM incidents")
    conn.execute("DELETE FROM rounds")
    total = 0
    for row in conn.execute("SELECT id FROM sessions"):
        sid = row["id"]
        msgs = load_session(conn, sid)
        for inc in find_incidents(msgs):
            cur = conn.execute(
                """INSERT INTO incidents(session_id, start_idx, end_idx, wasted_turns,
                   heat, signals, status) VALUES(?,?,?,?,?,?, 'pending')""",
                (sid, inc["start_idx"], inc["end_idx"], inc["wasted_turns"],
                 inc["heat"], inc["signals"]),
            )
            # round candidate: sustained opposition (>=2 redirect turns)
            if inc.get("n_redirects", 0) >= 2:
                conn.execute(
                    "INSERT INTO rounds(incident_id, turns) VALUES(?,?)",
                    (cur.lastrowid, inc["wasted_turns"]),
                )
            total += 1
    conn.commit()
    return total
