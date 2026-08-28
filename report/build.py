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


def _heat_class(t):
    return "h-hi" if t >= 38.5 else "h-mid" if t >= 37.5 else "h-lo"


def _day_label(max_temp):
    if max_temp >= 38.5:
        return "한판 붙은 날"
    if max_temp >= 37.5:
        return "소소한 마찰"
    return "싸움 없음"


def _stat(n, label):
    return f'<div class="stat"><div class="n">{n}</div><div class="l">{_esc(label)}</div></div>'


def _scoreboard(conn):
    sess = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
    by_src = dict(conn.execute("SELECT source, COUNT(*) FROM sessions GROUP BY source").fetchall())
    inc = conn.execute("SELECT COUNT(*) c FROM incidents").fetchone()["c"]
    rounds = conn.execute("SELECT COUNT(*) c FROM rounds").fetchone()["c"]
    corrections = conn.execute("SELECT COALESCE(SUM(count),0) c FROM phrases WHERE side='user'").fetchone()["c"]
    max_heat = conn.execute("SELECT COALESCE(MAX(heat),36.0) h FROM incidents").fetchone()["h"]

    # outcome tally — null until judge
    outs = dict(conn.execute("SELECT outcome, COUNT(*) FROM rounds GROUP BY outcome").fetchall())
    judged = sum(v for k, v in outs.items() if k)

    src_str = ", ".join(f"{k}:{v}" for k, v in by_src.items()) or "—"
    cards = "".join([
        _stat(sess, f"세션 ({src_str})"),
        _stat(inc, "사건 후보"),
        _stat(rounds, "라운드 후보"),
        _stat(corrections, "정정 발화"),
        _stat(f'<span class="{_heat_class(max_heat)}">{max_heat:.1f}°C</span>', "최고 열기"),
    ])
    out_html = "<h2>스코어보드</h2>"
    out_html += f'<p class="sub">오늘: {_day_label(max_heat)}</p>'
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


def _span_users(conn, sid, start, end, n=4):
    """A few user turns in the incident span, for the expanded detail row."""
    rows = conn.execute(
        "SELECT text FROM messages WHERE session_id=? AND idx BETWEEN ? AND ? "
        "AND role='user' AND text IS NOT NULL ORDER BY idx LIMIT ?",
        (sid, start, end, n),
    ).fetchall()
    return [r["text"] for r in rows]


def _pivot(conn):
    def tbl(title, rows):
        if not rows:
            return ""
        body = f'<div class="pivot"><h3>{_esc(title)}</h3><table>'
        for label, num in rows:
            body += f'<tr><td>{_esc(str(label))}</td><td class="num">{num}</td></tr>'
        return body + "</table></div>"

    # blame (미판정 포함)
    blame_rows = conn.execute(
        "SELECT blame, COUNT(*) c FROM incidents GROUP BY blame ORDER BY c DESC"
    ).fetchall()
    blame = [(BLAME_KO.get(r["blame"], r["blame"]) if r["blame"] else "미판정", r["c"])
             for r in blame_rows]

    # project: 건수 + 평균 체온
    proj_rows = conn.execute(
        "SELECT s.project, COUNT(*) c, AVG(i.heat) t FROM incidents i "
        "JOIN sessions s ON i.session_id=s.id GROUP BY s.project ORDER BY c DESC LIMIT 8"
    ).fetchall()
    proj = [(os.path.basename(r["project"] or "") or "?",
             f'{r["c"]}건 · {r["t"]:.1f}°C') for r in proj_rows]

    # signal frequency
    sig_ct = {}
    for r in conn.execute("SELECT signals FROM incidents"):
        for s in json.loads(r["signals"] or "[]"):
            sig_ct[s] = sig_ct.get(s, 0) + 1
    sig = sorted(sig_ct.items(), key=lambda x: -x[1])

    out = "<h2>피벗</h2><div class='pivots'>"
    out += tbl("귀책", blame) + tbl("프로젝트", proj) + tbl("신호", sig)
    return out + "</div>"


def _blame_summary(incs):
    """e.g. '모델 2 · 프롬프트 1' over a session's incidents (미판정 included)."""
    ct = {}
    for i in incs:
        k = BLAME_KO.get(i["blame"], i["blame"]) if i["blame"] else "미판정"
        ct[k] = ct.get(k, 0) + 1
    return " · ".join(f"{k} {v}" for k, v in sorted(ct.items(), key=lambda x: -x[1]))


def _incident_detail(conn, i):
    hc = _heat_class(i["heat"])
    blame = BLAME_KO.get(i["blame"], i["blame"]) if i["blame"] else "미판정"
    signals = ", ".join(json.loads(i["signals"] or "[]"))
    d = (f'<div class="inc-hd"><span class="temp {hc}">{i["heat"]:.1f}°C</span> '
         f'<span class="tag">{_esc(signals)}</span> '
         f'<span class="tag">{_esc(blame)}</span> '
         f'<span class="tag">낭비 {i["wasted_turns"]}턴</span></div>')
    for u in _span_users(conn, i["session_id"], i["start_idx"], i["end_idx"]):
        d += f'<div class="evi">🗣 {_esc(u[:200])}</div>'
    if i["suggestion"]:
        d += f'<div class="evi">💡 {_esc(i["suggestion"])}</div>'
    if i["evidence"]:
        d += f'<div class="evi">근거: {_esc(i["evidence"])}</div>'
    if i["counter_evidence"]:
        d += f'<div class="evi">반대: {_esc(i["counter_evidence"])}</div>'
    return d


def _incidents(conn):
    rows = conn.execute(
        "SELECT i.*, s.project FROM incidents i JOIN sessions s ON i.session_id=s.id "
        "ORDER BY i.heat DESC, i.wasted_turns DESC"
    ).fetchall()
    if not rows:
        return "<h2>사건 분석</h2><p class='sub'>사건 없음.</p>"

    # group by session (a session belongs to one project)
    groups = {}
    for i in rows:
        groups.setdefault(i["session_id"], []).append(i)
    # session summaries, sorted by hottest incident
    sessions = []
    for sid, incs in groups.items():
        sessions.append({
            "sid": sid, "project": incs[0]["project"],
            "max_heat": max(x["heat"] for x in incs),
            "turns": sum(x["wasted_turns"] for x in incs),
            "n": len(incs), "incs": incs,
        })
    sessions.sort(key=lambda s: (-s["max_heat"], -s["turns"]))

    head = ('<h2>사건 분석 <span class="sub">— 세션별 (행 클릭 시 개별 사건)</span></h2>'
            '<table class="inc"><thead><tr>'
            '<th data-key="temp" data-type="num" class="sorted">최고체온</th>'
            '<th data-key="proj">프로젝트</th>'
            '<th data-key="sess">세션</th>'
            '<th data-key="n" data-type="num">사건</th>'
            '<th data-key="turns" data-type="num">낭비턴</th>'
            '<th data-key="blame">귀책</th>'
            '</tr></thead><tbody>')
    trs = []
    for s in sessions:
        proj = os.path.basename(s["project"] or "") or "?"
        hc = _heat_class(s["max_heat"])
        bsum = _blame_summary(s["incs"])
        trs.append(
            '<tr class="row">'
            f'<td class="temp {hc}" data-sort="{s["max_heat"]}">{s["max_heat"]:.1f}°C</td>'
            f'<td data-sort="{_esc(proj)}">{_esc(proj)}</td>'
            f'<td class="muted" data-sort="{s["sid"][:8]}">{s["sid"][:8]}</td>'
            f'<td class="num" data-sort="{s["n"]}">{s["n"]}</td>'
            f'<td class="num" data-sort="{s["turns"]}">{s["turns"]}</td>'
            f'<td data-sort="{_esc(bsum)}">{_esc(bsum)}</td>'
            '</tr>'
        )
        det = "".join(f'<div class="inc-item">{_incident_detail(conn, i)}</div>'
                      for i in s["incs"])
        trs.append(f'<tr class="detail"><td colspan="6">{det}</td></tr>')
    return head + "".join(trs) + "</tbody></table>"


def build(conn, out_dir, date_label=None):
    date_label = date_label or datetime.now().strftime("%Y-%m-%d")
    span = conn.execute(
        "SELECT MIN(started_at) a, MAX(ended_at) b FROM sessions"
    ).fetchone()
    if span and span["a"]:
        date_label += f'  ·  {span["a"][:10]} ~ {span["b"][:10]}'

    body = _scoreboard(conn) + _phrases(conn) + _pivot(conn) + _incidents(conn)
    with open(TPL, encoding="utf-8") as f:
        tpl = f.read()
    doc = tpl.replace("{DATE}", _esc(date_label)).replace("{BODY}", body)

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, datetime.now().strftime("%Y-%m-%d") + ".html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path
