"""Snapshot the CLAUDE.md / project-instruction config, hashed for versioning.

SPEC 3: the judge needs the config that was in effect at session time. We only
have the *current* files on disk, so v1 stamps every session collected in a run
with one current snapshot. Per-session-time config is a later refinement.
"""

import os
import hashlib
import json

HOME = os.path.expanduser("~")
GLOBAL_MD = os.path.join(HOME, ".claude", "CLAUDE.md")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def snapshot(conn, project_dir=None):
    """Capture current config, upsert into config_versions, return its id."""
    global_md = _read(GLOBAL_MD)
    project_md = _read(os.path.join(project_dir, "CLAUDE.md")) if project_dir else ""
    payload = global_md + "\x00" + project_md
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    row = conn.execute("SELECT id FROM config_versions WHERE hash=?", (h,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        """INSERT INTO config_versions(hash, captured_at, global_md, project_md, instructions, skills_json)
           VALUES(?, datetime('now'), ?, ?, ?, ?)""",
        (h, global_md, project_md, "", json.dumps([])),
    )
    return cur.lastrowid


def stamp_sessions(conn, config_version_id):
    """Attach the snapshot to any session lacking one."""
    conn.execute(
        "UPDATE sessions SET config_version_id=? WHERE config_version_id IS NULL",
        (config_version_id,),
    )
