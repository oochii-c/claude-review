"""Blame judgment via a separate Claude API call (SPEC 5.2).

Gated: needs the `anthropic` SDK plus resolvable credentials (ANTHROPIC_API_KEY
or an `ant auth login` profile). If either is missing, judge_all() is a no-op so
the rest of the pipeline (collect -> detect -> report) still runs.

The judge is a fresh instance, not a conversation participant (bias control).
It must cite evidence and give one counter-argument. It asks first whether the
config already carried an instruction that would have prevented the failure.
"""

import json
import os
import shutil
import subprocess

MODEL = "claude-opus-4-8"

BLAME_TYPES = ["model", "prompt", "config", "env"]

SYSTEM = """You judge who is responsible for a failed exchange between a user and
Claude (the AI coding assistant), from the transcript and the config in effect.

Blame is exactly one of:
- model: ignored instructions, wrong fact/code, guessed without confirming, exceeded scope
- prompt: ambiguous request, missing context, mid-task goal change, several asks in one message
- config: the instruction files were absent / vague / conflicting / stale / excessive
- env: tool error, permissions, network

Judge in this order: FIRST ask whether an instruction in the config would have
prevented this failure. If one existed and was ignored -> model. If none existed
or it was wrong/ambiguous -> config or prompt. If the same failure recurs across
sessions, lean toward config.

You are not a participant. Cite the transcript verbatim in `evidence`. Give one
honest reason the opposite verdict could hold in `counter_evidence`. Score tone
intensity as a body temperature in `heat` (Celsius): 36.0 = calm, 36-37.5 is
normal, mild pushback ~37.5, a heated argument 38.5+, fury 40+."""

SCHEMA = {
    "type": "object",
    "properties": {
        "blame": {"type": "string", "enum": BLAME_TYPES},
        "type": {"type": "string"},
        "confidence": {"type": "number"},
        "evidence": {"type": "string"},
        "counter_evidence": {"type": "string"},
        "suggestion": {"type": "string"},
        "heat": {"type": "number"},
    },
    "required": [
        "blame",
        "type",
        "confidence",
        "evidence",
        "counter_evidence",
        "suggestion",
        "heat",
    ],
    "additionalProperties": False,
}


def _backend():
    """Pick a judgment backend: API key -> SDK; else the logged-in `claude` CLI
    (uses the Claude Code subscription, no separate API billing); else None."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401

            return "sdk"
        except ImportError:
            pass
    if shutil.which("claude"):
        return "cli"
    return None


def _extract_json(text):
    """Parse a JSON object out of model text, tolerating ```json fences."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    return json.loads(t)


def _invoke_sdk(user):
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        messages=[{"role": "user", "content": user}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def _invoke_cli(user):
    prompt = (
        SYSTEM
        + "\n\n"
        + user
        + "\n\nOutput ONLY a JSON object with exactly these keys: "
        + "blame, type, confidence, evidence, counter_evidence, suggestion, heat. "
        + "No prose, no code fences."
    )
    proc = subprocess.run(
        ["claude", "-p", "--output-format", "json", "--model", MODEL],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr.strip()[:200]}")
    envelope = json.loads(proc.stdout)
    return _extract_json(envelope["result"])


def _span_text(conn, sid, start, end):
    rows = conn.execute(
        "SELECT idx, role, text, tool_calls_json FROM messages "
        "WHERE session_id=? AND idx BETWEEN ? AND ? ORDER BY idx",
        (sid, start, end),
    ).fetchall()
    lines = []
    for r in rows:
        who = "USER" if r["role"] == "user" else "CLAUDE"
        txt = r["text"] or ""
        if r["tool_calls_json"]:
            tools = json.loads(r["tool_calls_json"])
            txt += " " + " ".join(f"[{t.get('name')}]" for t in tools)
        lines.append(f"{who}: {txt.strip()[:600]}")
    return "\n".join(lines)


def _feedback_examples():
    """Few-shot overrides the user has flipped (SPEC 5.2 bias control)."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "feedback.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)[:5]
    except Exception:
        return []


def judge_incident(backend, span_text, config_text, feedback):
    fb = ""
    if feedback:
        fb = (
            "\n\nPrior human corrections to your judgments (follow them):\n"
            + json.dumps(feedback, ensure_ascii=False)
        )
    user = (
        f"CONFIG IN EFFECT:\n{config_text[:8000]}\n\n"
        f"FAILED EXCHANGE:\n{span_text}{fb}\n\n"
        "Return the judgment as JSON."
    )
    return _invoke_sdk(user) if backend == "sdk" else _invoke_cli(user)


def judge_all(conn, limit=None):
    """Judge pending incidents. Returns (judged, message)."""
    backend = _backend()
    if backend is None:
        return 0, "skipped: no API key and no `claude` CLI on PATH"

    feedback = _feedback_examples()
    q = (
        "SELECT i.*, s.project FROM incidents i JOIN sessions s ON i.session_id=s.id "
        "WHERE i.blame IS NULL ORDER BY i.heat DESC, i.wasted_turns DESC"
    )
    rows = conn.execute(q).fetchall()
    if limit:
        rows = rows[:limit]

    judged = 0
    for inc in rows:
        cfg = conn.execute(
            "SELECT global_md, project_md FROM config_versions cv "
            "JOIN sessions s ON s.config_version_id=cv.id WHERE s.id=?",
            (inc["session_id"],),
        ).fetchone()
        config_text = (
            ((cfg["global_md"] or "") + "\n" + (cfg["project_md"] or "")) if cfg else ""
        )
        span = _span_text(conn, inc["session_id"], inc["start_idx"], inc["end_idx"])
        try:
            v = judge_incident(backend, span, config_text, feedback)
        except Exception as e:
            print(f"  judge error on incident {inc['id']}: {e}")
            continue
        if "blame" not in v:
            print(f"  judge skipped incident {inc['id']}: no blame in output")
            continue
        v.setdefault("type", "")
        v.setdefault("confidence", 0.5)
        v.setdefault("evidence", "")
        v.setdefault("counter_evidence", "")
        v.setdefault("suggestion", "")
        v.setdefault("heat", inc["heat"])
        conn.execute(
            """UPDATE incidents SET blame=?, type=?, confidence=?, evidence=?,
               counter_evidence=?, suggestion=?, heat=? WHERE id=?""",
            (
                v["blame"],
                v["type"],
                v["confidence"],
                v["evidence"],
                v["counter_evidence"],
                v["suggestion"],
                v["heat"],
                inc["id"],
            ),
        )
        judged += 1
    conn.commit()
    return judged, f"judged {judged} incidents"
