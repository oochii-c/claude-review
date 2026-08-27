"""Render review.db into a static HTML report (SPEC 6).

Sections, top to bottom: scoreboard, frequent phrases, incident analysis.
Trends are deferred. Blame/outcome show 미판정 until judge.py has run.
"""
import os
import json
import html
from datetime import datetime

BLAME_KO = {"model": "모델", "prompt": "프롬프트", "config": "설정", "env": "환경"}
OUTCOME_KO = {
    "user_win": "내가 이김", "model_win": "너가 이김",
    "good_fight": "좋은 승부", "unresolved": "미결",
}
TPL = os.path.join(os.path.dirname(__file__), "template.html")


def _esc(s):
    return html.escape(s or "")


def _heat_class(h):
    return "h-hi" if h >= 7 else "h-mid" if h >= 4 else "h-lo"


def _stat(n, label):
    return f'<div class="stat"><div class="n">{n}</div><div class="l">{_esc(label)}</div></div>'


def _scoreboard(conn):
    sess = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
    by_src = dict(conn.execute("SELECT source, COUNT(*) FROM sessions GROUP BY source").fetchall())
    inc = conn.execute("SELECT COUNT(*) c FROM incidents").fetchone()["c"]
    rounds = conn.execute("SELECT COUNT(*) c FROM rounds").fetchone()["c"]
    corrections = conn.execute("SELECT COALESCE(SUM(count),0) c FROM phrases WHERE side='user'").fetchone()["c"]
    max_heat = conn.execute("SELECT COALESCE(MAX(heat),0) h FROM incidents").fetchone()["h"]

    # outcome tally — null until judge
    outs = dict(conn.execute("SELECT outcome, COUNT(*) FROM rounds GROUP BY outcome").fetchall())
    judged = sum(v for k, v in outs.items() if k)

    src_str = ", ".join(f"{k}:{v}" for k, v in by_src.items()) or "—"
    cards = "".join([
        _stat(sess, f"세션 ({src_str})"),
        _stat(inc, "사건 후보"),
        _stat(rounds, "라운드 후보"),
        _stat(corrections, "정정 발화"),
        _stat(f'<span class="{_heat_class(max_heat)}">{max_heat}</span>', "최고 열기"),
    ])
    out_html = "<h2>스코어보드</h2>"
    out_html += f'<div class="grid">{cards}</div>'
    if judged:
        tally = " · ".join(f"{OUTCOME_KO.get(k, k)} {v}" for k, v in outs.items() if k)
        good = outs.get("good_fight", 0)
        rate = f"{good/judged*100:.0f}%" if judged else "—"
        out_html += f'<p class="sub" style="margin-top:12px">승부: {tally} · 좋은 승부 비율 {rate}</p>'
    else:
        out_html += '<p class="sub pending" style="margin-top:12px">승부 결과: 미판정 (judge 미실행)</p>'
    return out_html


def _phrases(conn):
    rows = conn.execute(
        "SELECT canonical, SUM(count) c, examples_json FROM phrases WHERE side='user' "
        "GROUP BY canonical ORDER BY c DESC LIMIT 5"
    ).fetchall()
    if not rows:
        return ""
    body = "<h2>자주 한 말 (나)</h2><table><tr><th>표현</th><th>횟수</th><th>예시</th></tr>"
    for r in rows:
        ex = json.loads(r["examples_json"] or "[]")
        ex_str = _esc(ex[0]) if ex else ""
        body += f'<tr><td>{_esc(r["canonical"])}</td><td>{r["c"]}</td><td class="muted">{ex_str}</td></tr>'
    return body + "</table>"


def _incidents(conn, limit=40):
    rows = conn.execute(
        "SELECT i.*, s.project FROM incidents i JOIN sessions s ON i.session_id=s.id "
        "ORDER BY i.wasted_turns DESC, i.heat DESC LIMIT ?", (limit,)
    ).fetchall()
    if not rows:
        return "<h2>사건 분석</h2><p class='sub'>사건 없음.</p>"
    body = "<h2>사건 분석</h2>"
    for i in rows:
        proj = os.path.basename(i["project"] or "") or "?"
        signals = ", ".join(json.loads(i["signals"] or "[]"))
        hc = _heat_class(i["heat"])
        blame = (f'<span class="tag">{BLAME_KO.get(i["blame"], i["blame"])}</span>'
                 if i["blame"] else '<span class="tag pending">미판정</span>')
        body += '<div class="card"><div class="top">'
        body += f'<span class="heat {hc}">열기 {i["heat"]}</span>'
        body += f'<span class="tag">{_esc(proj)}</span>'
        body += f'<span class="tag">낭비 {i["wasted_turns"]}턴</span>'
        body += f'<span class="tag">{_esc(signals)}</span>'
        body += blame
        body += "</div>"
        if i["suggestion"]:
            body += f'<div>{_esc(i["suggestion"])}</div>'
        if i["evidence"]:
            body += f'<div class="evi">{_esc(i["evidence"])}</div>'
        if i["counter_evidence"]:
            body += f'<div class="evi">반대 근거: {_esc(i["counter_evidence"])}</div>'
        body += "</div>"
    return body


def build(conn, out_dir, date_label=None):
    date_label = date_label or datetime.now().strftime("%Y-%m-%d")
    span = conn.execute(
        "SELECT MIN(started_at) a, MAX(ended_at) b FROM sessions"
    ).fetchone()
    if span and span["a"]:
        date_label += f'  ·  {span["a"][:10]} ~ {span["b"][:10]}'

    body = _scoreboard(conn) + _phrases(conn) + _incidents(conn)
    with open(TPL, encoding="utf-8") as f:
        tpl = f.read()
    doc = tpl.replace("{DATE}", _esc(date_label)).replace("{BODY}", body)

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, datetime.now().strftime("%Y-%m-%d") + ".html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path
