"""Parse ~/.claude/projects/**/*.jsonl into (session, messages) records.

Reuses youbad's JSONL parsing. Skips meta line types; keeps real user speech
and assistant turns. Tool-result-only user turns are plumbing, not speech, and
are dropped. Tool-call inputs are truncated (SPEC section 9, default 2KB).
"""

import json
import os
import glob
from datetime import datetime

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
TRUNC = 2048  # tool-call payload cap

META_TEXT_PREFIXES = ("<", "[Request", "[Image")


def _user_text(m):
    c = m.get("content")
    if isinstance(c, str):
        txt = c
    elif isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return None  # tool plumbing, not speech
        txt = " ".join(
            b.get("text", "")
            for b in c
            if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        return None
    txt = (txt or "").strip()
    if not txt or txt.startswith(META_TEXT_PREFIXES):
        return None
    if "system-reminder" in txt[:40] or txt.startswith("Caveman"):
        return None
    return txt


def _assistant(m):
    """Return (text, tool_calls) for an assistant message; text may be ''."""
    c = m.get("content")
    if isinstance(c, str):
        return c.strip(), []
    if not isinstance(c, list):
        return "", []
    texts, tools = [], []
    for b in c:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            texts.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            inp = json.dumps(b.get("input", {}), ensure_ascii=False)[:TRUNC]
            tools.append({"name": b.get("name"), "input": inp})
    return " ".join(t for t in texts if t).strip(), tools


def _ts(o):
    ts = o.get("timestamp")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def parse_file(path):
    """Return (session_dict, [message_dict]) or (None, []) if no real content."""
    sid = os.path.basename(path)[:-6]  # strip .jsonl
    project, model = None, None
    times = []
    rows = []  # (role, ts_iso, text, tool_calls)
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("cwd") and not project:
            project = o.get("cwd")
        t = o.get("type")
        m = o.get("message", {}) if isinstance(o.get("message"), dict) else {}
        ts = _ts(o)
        if ts:
            times.append(ts)
        if t == "user" and not o.get("isMeta") and m.get("role") == "user":
            txt = _user_text(m)
            if txt:
                rows.append(("user", o.get("timestamp"), txt, None))
        elif t == "assistant" and m.get("role") == "assistant":
            if not model and m.get("model"):
                model = m.get("model")
            text, tools = _assistant(m)
            if text or tools:
                rows.append(
                    (
                        "assistant",
                        o.get("timestamp"),
                        text,
                        json.dumps(tools, ensure_ascii=False) if tools else None,
                    )
                )
    if not rows:
        return None, []
    session = {
        "id": sid,
        "source": "code",
        "project": project,
        "started_at": min(times).isoformat() if times else None,
        "ended_at": max(times).isoformat() if times else None,
        "model": model,
    }
    msgs = [
        {
            "session_id": sid,
            "idx": i,
            "role": r,
            "ts": ts,
            "text": tx,
            "tool_calls_json": tc,
        }
        for i, (r, ts, tx, tc) in enumerate(rows)
    ]
    return session, msgs


def iter_sessions(since=None, projects=PROJECTS):
    """Yield (session, msgs) for each session file. `since`: datetime; only files
    modified at/after it are parsed (None = all)."""
    for f in sorted(glob.glob(os.path.join(projects, "*", "*.jsonl"))):
        if since is not None:
            mtime = datetime.fromtimestamp(os.path.getmtime(f))
            if mtime < since:
                continue
        session, msgs = parse_file(f)
        if session:
            yield session, msgs
