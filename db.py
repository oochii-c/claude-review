"""SQLite store for court. Schema per SPEC.md section 4."""

import sqlite3
import os

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,
    source            TEXT,           -- code | web | desktop
    project           TEXT,
    started_at        TEXT,
    ended_at          TEXT,
    model             TEXT,
    config_version_id INTEGER
);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT,
    idx             INTEGER,
    role            TEXT,             -- user | assistant
    ts              TEXT,
    text            TEXT,
    tool_calls_json TEXT,
    UNIQUE(session_id, idx)
);
CREATE TABLE IF NOT EXISTS config_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    hash         TEXT UNIQUE,
    captured_at  TEXT,
    global_md    TEXT,
    project_md   TEXT,
    instructions TEXT,
    skills_json  TEXT
);
CREATE TABLE IF NOT EXISTS incidents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT,
    start_idx      INTEGER,
    end_idx        INTEGER,
    signals        TEXT,             -- rule-detected markers (JSON)
    blame          TEXT,             -- model | prompt | config | env
    type           TEXT,
    confidence     REAL,
    evidence       TEXT,
    counter_evidence TEXT,
    wasted_turns   INTEGER,
    suggestion     TEXT,
    heat           REAL,             -- body temperature in Celsius (36.0 = calm)
    status         TEXT DEFAULT 'pending'   -- pending | approved | overridden
);
CREATE TABLE IF NOT EXISTS rounds (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id  INTEGER,
    outcome      TEXT,               -- user_win | model_win | good_fight | unresolved
    turns        INTEGER,
    end_utterance TEXT
);
CREATE TABLE IF NOT EXISTS phrases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    side         TEXT,               -- user | model
    canonical    TEXT,
    count        INTEGER,
    week         TEXT,
    examples_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_inc_session ON incidents(session_id);
"""

DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "review.db")


def connect(path=DEFAULT_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_session(conn, s):
    conn.execute(
        """INSERT INTO sessions(id, source, project, started_at, ended_at, model, config_version_id)
           VALUES(:id,:source,:project,:started_at,:ended_at,:model,:config_version_id)
           ON CONFLICT(id) DO UPDATE SET
             source=excluded.source, project=excluded.project,
             started_at=excluded.started_at, ended_at=excluded.ended_at,
             model=excluded.model, config_version_id=excluded.config_version_id""",
        {"config_version_id": None, **s},
    )


def replace_messages(conn, session_id, msgs):
    """Idempotent: wipe this session's messages, reinsert."""
    conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
    conn.executemany(
        """INSERT INTO messages(session_id, idx, role, ts, text, tool_calls_json)
           VALUES(:session_id,:idx,:role,:ts,:text,:tool_calls_json)""",
        msgs,
    )
