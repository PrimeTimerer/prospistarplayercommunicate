#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline HTML + console rendering of a generated feed.

No external assets: system font stacks only (no Google Fonts), so the page
opens fully offline. Theme-aware (light/dark) via CSS tokens.
"""
import html

from prose_format import normalize_generated_markdown

_CSS = """
:root{--ground:#eef1f5;--surface:#fff;--s2:#f4f6f9;--s3:#e9edf2;--ink:#12181f;
--soft:#37424f;--muted:#5a6b7d;--line:#dde3ea;--accent:#c85200;--hot:#d81f13;
--hotbg:#fbe7e5;--good:#1a7f37;--info:#0a5fb4;--warn:#b06a00;--purple:#6b3fb0;
--shadow:0 1px 2px rgba(16,24,32,.06),0 8px 24px rgba(16,24,32,.08)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
--ground:#0b111b;--surface:#131c2a;--s2:#1b2636;--s3:#22303f;--ink:#eaf0f7;
--soft:#c3cedb;--muted:#8595a8;--line:#273548;--accent:#ff9d2e;--hot:#ff5347;
--hotbg:#341615;--good:#3fb950;--info:#4aa8ff;--warn:#ffb020;--purple:#b98cff;
--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.45)}}
:root[data-theme=dark]{--ground:#0b111b;--surface:#131c2a;--s2:#1b2636;--s3:#22303f;
--ink:#eaf0f7;--soft:#c3cedb;--muted:#8595a8;--line:#273548;--accent:#ff9d2e;
--hot:#ff5347;--hotbg:#341615;--good:#3fb950;--info:#4aa8ff;--warn:#ffb020;--purple:#b98cff;
--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.45)}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);line-height:1.62;
font-family:"Malgun Gothic","Apple SD Gothic Neo",system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:0 16px 72px}
.impact{font-family:"Arial Black","Malgun Gothic",system-ui,sans-serif;font-weight:800;letter-spacing:-.01em}
.tnum{font-variant-numeric:tabular-nums}
.bar{position:sticky;top:0;z-index:9;background:color-mix(in srgb,var(--ground) 88%,transparent);
backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.bar-in{max-width:760px;margin:0 auto;padding:11px 16px;display:flex;align-items:center;gap:11px}
.brand{font-weight:800;letter-spacing:.12em;text-transform:uppercase;font-size:14px}
.brand b{color:var(--accent)}
.live{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--hot)}
.live .d{width:7px;height:7px;border-radius:50%;background:var(--hot);animation:p 1.6s infinite}
@keyframes p{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--hot) 55%,transparent)}70%{box-shadow:0 0 0 6px transparent}100%{box-shadow:0 0 0 0 transparent}}
.sp{flex:1}.tg{border:1px solid var(--line);background:var(--s2);color:var(--soft);border-radius:99px;padding:5px 11px;font-size:12px;cursor:pointer;font-family:inherit}
.rev{opacity:0;transform:translateY(12px);animation:r .55s cubic-bezier(.2,.7,.2,1) forwards}
@keyframes r{to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.rev{animation:none;opacity:1;transform:none}.live .d{animation:none}}
.hero{margin-top:20px;background:radial-gradient(120% 140% at 100% 0%,color-mix(in srgb,var(--accent) 13%,transparent),transparent 55%),var(--surface);
border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:var(--shadow)}
.htop{padding:20px 20px 8px}
.eyebrow{text-transform:uppercase;letter-spacing:.16em;font-size:10px;color:var(--muted);font-weight:700}
.team{color:var(--soft);font-size:13px;margin-top:9px;font-weight:600}
.name{font-size:clamp(34px,8vw,56px);line-height:1;margin:3px 0 8px;text-wrap:balance}
.meta{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;color:var(--muted);font-size:13px}
.tone{display:inline-flex;align-items:center;gap:6px;background:var(--hotbg);color:var(--hot);
border:1px solid color-mix(in srgb,var(--hot) 40%,transparent);font-weight:800;letter-spacing:.06em;padding:4px 11px;border-radius:99px;font-size:13px}
.evt{margin:14px 20px 0;padding:12px 14px;background:var(--s2);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:8px;font-size:14px;color:var(--soft)}
.evt b{color:var(--ink)}
.persona{margin:11px 0 2px;font-size:12.5px;color:var(--muted)}
.bug{display:grid;grid-template-columns:repeat(auto-fit,minmax(88px,1fr));gap:1px;background:var(--line);margin-top:16px;border-top:1px solid var(--line)}
.stat{background:var(--s2);padding:13px 10px;text-align:center}
.stat .v{font-size:clamp(24px,5.5vw,34px);line-height:1;font-weight:800}
.stat .v.hot{color:var(--hot)}.stat .v.acc{color:var(--accent)}
.stat .l{text-transform:uppercase;letter-spacing:.09em;font-size:9px;color:var(--muted);margin-top:6px;font-weight:700}
.lines{padding:13px 20px 18px;display:grid;gap:7px}
.line{display:flex;gap:9px;font-size:13.5px}.line .k{font-weight:800;color:var(--accent);min-width:38px;font-size:12px}.line .val{color:var(--soft)}
.sec{margin-top:30px}
.sh{display:flex;align-items:center;gap:9px;margin-bottom:12px}
.sh .t{font-weight:800;text-transform:uppercase;letter-spacing:.12em;font-size:11px;color:#fff;background:var(--accent);padding:4px 9px;border-radius:6px}
.sh .t.c{background:var(--info)}.sh h2{margin:0;font-size:17px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);overflow:hidden}
.stack{display:grid;gap:14px}
.art{padding:18px 20px}
.art .o{display:inline-flex;gap:7px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);border:1px solid var(--line);padding:3px 9px;border-radius:99px}
.art h3{font-size:clamp(19px,4.4vw,25px);line-height:1.22;margin:12px 0 5px;text-wrap:balance}
.art .sub{color:var(--accent);font-size:13px;font-weight:600;margin-bottom:10px}
.art p{margin:0 0 9px;color:var(--soft);font-size:14.5px;max-width:62ch}.art p:last-child{margin:0}
.th{--forum:#61738a;overflow:hidden;border-top:3px solid var(--forum)}
.th.dc{--forum:#365fd4}.th.fmk{--forum:#b5453f}.th.mlb{--forum:#2876bb}
.thh{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:6px 10px;padding:11px 14px;border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--forum) 10%,var(--s2))}
.chip{font-weight:800;font-size:10px;letter-spacing:.06em;text-transform:uppercase;padding:4px 8px;border-radius:6px;white-space:nowrap}
.chip.dc{background:#365fd4;color:#fff;border:1px solid #6f8ced}
.chip.fmk{background:#b5453f;color:#fff;border:1px solid #e18982}
.chip.mlb{background:#1f6feb22;color:var(--info);border:1px solid #1f6feb55}
.thh .tt{font-weight:700;font-size:14.5px;line-height:1.35}
.thmeta{font-size:9px;color:var(--muted);white-space:nowrap}.thbrand{grid-column:1/-1;font-size:9px;color:var(--forum);font-weight:800;letter-spacing:.08em;text-transform:uppercase}
.cmt{display:grid;grid-template-columns:14px minmax(88px,120px) 1fr 42px auto;gap:8px;padding:9px 14px;border-bottom:1px solid var(--line);align-items:start}
.cmt.opener{padding-top:12px;padding-bottom:12px;background:color-mix(in srgb,var(--forum) 6%,var(--surface))}
.cmt:last-child{border-bottom:0}
.branch{font-size:10px;color:var(--forum);text-align:center}.who{font-size:11px;font-weight:700;color:var(--forum);white-space:nowrap;max-width:120px;overflow:hidden;text-overflow:ellipsis}
.txt{font-size:13px;color:var(--soft)}.opener .txt{font-weight:650;color:var(--ink)}
.when{font-size:9px;color:var(--muted);white-space:nowrap}.up{font-size:10px;font-weight:800;color:var(--good);white-space:nowrap}
.th.dc .cmt{padding-top:7px;padding-bottom:7px}.th.mlb .cmt{padding-top:11px;padding-bottom:11px}.th.mlb .txt{line-height:1.72}
.soc{padding:16px 18px}.soc .so{display:flex;justify-content:space-between;gap:10px;color:var(--muted);font-size:11px;font-weight:700}
.soc .spost{margin:11px 0;color:var(--ink);font-size:14.5px}.sr{border-top:1px solid var(--line)}
.sr div{display:grid;grid-template-columns:auto 1fr auto;gap:10px;padding:9px 0;border-bottom:1px solid var(--line);font-size:12px;color:var(--soft)}
.sr div:last-child{border-bottom:0}.sr b{color:var(--muted);font-size:11px}.sr em{color:var(--good);font-style:normal;font-size:10px}
.foot{margin-top:36px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;text-align:center;line-height:1.7}
.foot b{color:var(--soft)}
@media(max-width:560px){.thh{grid-template-columns:auto 1fr}.thmeta{grid-column:1/-1}.cmt{grid-template-columns:12px 1fr auto}.branch{grid-row:1/4}.who{grid-column:2}.when{grid-column:3}.txt{grid-column:2/-1}.up{grid-column:2/-1}}
"""

_JS = """
(function(){var r=document.documentElement,b=document.getElementById('tg');
function c(){var t;try{t=localStorage.getItem('smf')}catch(e){}return t||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light')}
r.setAttribute('data-theme',c());
b&&b.addEventListener('click',function(){var n=r.getAttribute('data-theme')==='dark'?'light':'dark';r.setAttribute('data-theme',n);try{localStorage.setItem('smf',n)}catch(e){}})})();
"""

_E = html.escape

_BOARD_SKINS = {
    "dc": ("DC", "디시인사이드 · 프로야구 갤러리", "개념"),
    "fmk": ("FMK", "에펨코리아 · 야구 게시판", "추천"),
    "mlb": ("M", "MLBPARK · 불펜", "공감"),
}


def _stat_bug(stats):
    tiles = []
    if stats.get("bat_AVG"):
        tiles.append(("acc", f".{int(round(stats['bat_AVG']*1000)):03d}", "AVG"))
    if stats.get("bat_HR"):
        tiles.append(("", str(stats["bat_HR"]), "HR"))
    if stats.get("pit_K"):
        tiles.append(("", str(stats["pit_K"]), "K"))
    if stats.get("pit_W") is not None:
        tiles.append(("hot", str(stats["pit_W"]), "WINS"))
    if stats.get("pit_IP"):
        tiles.append(("", str(stats["pit_IP"]), "IP"))
    return "".join(
        f'<div class="stat"><div class="v {c} tnum">{_E(v)}</div><div class="l">{_E(l)}</div></div>'
        for c, v, l in tiles[:5])


def render_html(feed):
    p, a, ev = feed["player"], feed["assess"], feed["event"]
    d = ev["date"]
    date_s = f"{d.get('year')}.{d.get('month')}.{d.get('day')}"
    tone_fire = "🔥" if a["tone"] == "폭발" else ""

    evt_txt = ""
    kind_ko = {"NEW_GAME": "새 경기", "SEASON_UPDATE": "기록 갱신",
               "WORLD_INIT": "세계 생성", "CORRECTION": "정정",
               "REST_DAY": "휴식일", "NO_CHANGE": "변화 없음"}.get(ev["kind"], ev["kind"])
    bits = [f"<b>{_E(kind_ko)}</b>"]
    if ev["game_lines"]:
        bits += [_E(x) for x in ev["game_lines"]]
    if ev["milestones"]:
        bits.append("🏆 " + _E(ev["milestones"][0][1]))
    evt_txt = " · ".join(bits)

    media_html = ""
    for art in feed["media"]:
        paras = "".join(f"<p>{_E(x)}</p>" for x in art["body"])
        media_html += (
            f'<article class="card art rev"><span class="o">{_E(art["flag"])} {_E(art["outlet"])}</span>'
            f'<h3 class="impact">{_E(art["title"])}</h3><div class="sub">{_E(art["sub"])}</div>{paras}</article>')

    boards_html = ""
    for b in feed["boards"]:
        code = b.get("code") if b.get("code") in _BOARD_SKINS else "generic"
        mark, brand, vote_label = _BOARD_SKINS.get(code, ("B", b.get("board", "커뮤니티"), "추천"))
        meta = b.get("thread_meta") or {}
        details = " · ".join(value for value in (
            f'작성 {_E(str(meta["posted_at"]))}' if meta.get("posted_at") else "",
            f'조회 {_E(str(meta["views"]))}' if meta.get("views") is not None else "",
            f'{_E(vote_label)} {_E(str(meta["recommendations"]))}' if meta.get("recommendations") is not None else "",
        ) if value)
        rows = ""
        for index, c in enumerate(b["comments"]):
            opener = c.get("is_opener") is True or index == 0
            up = f'{_E(vote_label)} {_E(str(c["up"]))}' if "up" in c else ""
            down = f' · 비추천 {_E(str(c["down"]))}' if c.get("down") else ""
            rows += (f'<div class="cmt {"opener" if opener else "reply"}"><span class="branch">{"●" if opener else "↳"}</span>'
                     f'<span class="who">{_E(c.get("author", "익명"))}</span><span class="txt">{_E(c.get("text", ""))}</span>'
                     f'<span class="when">{_E(str(c.get("posted_at", "")))}</span><span class="up">{up}{down}</span></div>')
        boards_html += (
            f'<div class="card th {code} rev"><div class="thh"><span class="thbrand">{_E(brand)} · 창작 반응</span>'
            f'<span class="chip {code}">{_E(mark)}</span><span class="tt">{_E(b.get("title", ""))}</span>'
            f'<span class="thmeta">{details or ("댓글 " + _E(str(len(b["comments"]))))}</span></div>{rows}</div>')

    social_html = ""
    for post in feed.get("social") or []:
        replies = "".join(
            f'<div><b>{_E(reply.get("author", "익명"))}</b><span>{_E(reply.get("text", ""))}</span>'
            f'<em>{("♡ " + _E(str(reply["reactions"]))) if "reactions" in reply else ""}</em></div>'
            for reply in post.get("replies") or []
        )
        reactions = f'♡ {_E(str(post["reactions"]))}' if "reactions" in post else ""
        social_html += (
            f'<article class="card soc rev"><div class="so"><span>{_E(post.get("platform", "텍스트 SNS"))} · '
            f'{_E(post.get("author", "가상 계정"))}</span><span>{reactions}</span></div>'
            f'<p class="spost">{_E(post.get("text", ""))}</p><div class="sr">{replies}</div></article>'
        )

    media_sec = (f'<section class="sec"><div class="sh rev"><span class="t">속보 · Breaking</span>'
                 f'<h2>언론 헤드라인</h2></div><div class="stack">{media_html}</div></section>'
                 if media_html else "")
    social_sec = (f'<section class="sec"><div class="sh rev"><span class="t c">텍스트 SNS</span>'
                  f'<h2>국내외 소셜 반응</h2></div><div class="stack">{social_html}</div></section>'
                  if social_html else "")

    return f"""<title>스타모드 피드</title>
<style>{_CSS}</style>
<div class="bar"><div class="bar-in"><span class="brand">스타모드 <b>피드</b></span>
<span class="live"><span class="d"></span>LIVE</span><span class="sp"></span>
<button class="tg" id="tg" aria-label="테마 전환">◐ 테마</button></div></div>
<div class="wrap">
<section class="hero rev">
<div class="htop"><div class="eyebrow">프로야구 스피리츠 · 스타모드 커뮤니티 시뮬레이션</div>
<div class="team">{_E(p['team'])} · {_E(date_s)} · {d.get('career_year')}년차</div>
<h1 class="name impact">{_E(p['name'])}</h1>
<div class="meta"><span class="tone">{tone_fire} {_E(a['tone'])}</span><span>{_E(p.get('pos',''))}</span>
<span>HEAT {feed['heat']}/10</span></div>
{('<div class="persona">🧬 선수 성향 · ' + _E(feed['persona']) + '</div>') if feed.get('persona') else ''}</div>
<div class="evt">{evt_txt}</div>
<div class="bug">{_stat_bug(feed.get('_stats') or {})}</div>
<div class="lines">{_lines_html(a)}</div>
</section>
{media_sec}
{social_sec}
<section class="sec"><div class="sh rev"><span class="t c">커뮤니티 반응</span><h2>국내외 게시판</h2></div>
<div class="stack">{boards_html}</div></section>
<div class="foot"><b>스타모드 피드</b> — StarPlayer.dat를 읽어 자동 생성한 <b>오프라인 가상 시뮬레이션</b>입니다.<br>
기사·댓글은 게임 데이터 기반 창작 텍스트이며 실제 보도·실존 인물의 발언이 아닙니다.</div>
</div>
<script>{_JS}</script>
"""


def _lines_html(a):
    out = ""
    if a.get("line_pit"):
        out += f'<div class="line"><span class="k">투수</span><span class="val tnum">{_E(a["line_pit"])}</span></div>'
    if a.get("line_bat"):
        out += f'<div class="line"><span class="k">타자</span><span class="val tnum">{_E(a["line_bat"])}</span></div>'
    return out


_NARR_CSS = """
:root{--ground:#eef1f5;--surface:#fff;--s2:#f4f6f9;--ink:#12181f;--soft:#37424f;
--muted:#5a6b7d;--line:#dde3ea;--accent:#c85200;--hot:#d81f13;--info:#0a5fb4}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--ground:#0b111b;
--surface:#131c2a;--s2:#1b2636;--ink:#eaf0f7;--soft:#c3cedb;--muted:#8595a8;
--line:#273548;--accent:#ff9d2e;--hot:#ff5347;--info:#4aa8ff}}
*{box-sizing:border-box}body{margin:0;background:var(--ground);color:var(--ink);
font-family:"Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif;line-height:1.7}
.wrap{max-width:760px;margin:0 auto;padding:24px 18px 80px}
.tagbar{font-size:11px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px}
h1{font-size:26px;margin:18px 0 6px;border-bottom:2px solid var(--accent);padding-bottom:6px}
h2{font-size:20px;margin:28px 0 10px;padding-left:11px;border-left:3px solid var(--accent);color:var(--accent);line-height:1.42}
h3{font-size:16px;margin:18px 0 6px}
hr{border:0;border-top:1px solid var(--line);margin:22px 0}
blockquote{margin:10px 0;padding:10px 14px;background:var(--s2);border-left:3px solid var(--info);
border-radius:6px;color:var(--soft);white-space:pre-wrap}
ul{padding-left:20px}li{margin:3px 0;color:var(--soft)}
p{margin:9px 0 15px;color:var(--soft);text-indent:1em}strong{color:var(--ink)}
.foot{margin-top:36px;padding-top:14px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;text-align:center}
"""


def render_narrative_html(md, model=""):
    """Lightweight, safe markdown -> themed HTML for the offline narrative."""
    md = normalize_generated_markdown(md)
    def esc(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def inline(s):
        # bold **...**
        parts = s.split("**")
        buf = ""
        for i, seg in enumerate(parts):
            buf += (f"<strong>{seg}</strong>" if i % 2 else seg)
        return buf

    out = []
    in_ul = False
    in_bq = False
    for raw in md.splitlines():
        line = raw.rstrip()
        s = line.lstrip()
        if in_bq and not s.startswith(">"):
            out.append("</blockquote>")
            in_bq = False
        if in_ul and not (s.startswith("- ") or s.startswith("* ")):
            out.append("</ul>")
            in_ul = False
        if not s:
            continue
        if s.startswith("### "):
            out.append(f"<h3>{inline(esc(s[4:]))}</h3>")
        elif s.startswith("## "):
            out.append(f"<h2>{inline(esc(s[3:]))}</h2>")
        elif s.startswith("# "):
            out.append(f"<h1>{inline(esc(s[2:]))}</h1>")
        elif s.startswith("---") or s.startswith("***"):
            out.append("<hr>")
        elif s.startswith(">"):
            if not in_bq:
                out.append("<blockquote>")
                in_bq = True
            out.append(inline(esc(s.lstrip(">").strip())))
        elif s.startswith("- ") or s.startswith("* "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{inline(esc(s[2:]))}</li>")
        else:
            out.append(f"<p>{inline(esc(s))}</p>")
    if in_bq:
        out.append("</blockquote>")
    if in_ul:
        out.append("</ul>")
    body = "\n".join(out)
    if str(model).startswith("gemini:"):
        tag = f"Google Gemini API 생성 · {esc(model)}"
    elif str(model).startswith("builtin:"):
        tag = "완전 오프라인 내장 서사 엔진"
    else:
        tag = f"오프라인 로컬 LLM 생성 · {esc(model)}" if model else "오프라인 로컬 LLM 생성"
    return (f"<title>스타모드 서사</title>\n<style>{_NARR_CSS}</style>\n"
            f'<div class="wrap"><div class="tagbar">{tag}</div>{body}'
            f'<div class="foot">게임 데이터 기반 창작 시뮬레이션 · 실제 보도가 아닙니다.</div></div>')


def render_built_in_narrative(feed, event=None):
    """Project the verified deterministic feed into readable long-form Markdown.

    This is the final, fully offline narrative fallback. It does not invent a
    new event or ask a model to paraphrase facts; it only reorganizes prose
    already produced by the deterministic feed engine and the current verified
    save event.
    """

    feed = feed if isinstance(feed, dict) else {}
    event = event if isinstance(event, dict) else {}
    snapshot = event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {}
    player = feed.get("player") if isinstance(feed.get("player"), dict) else snapshot.get("player") or {}
    feed_event = feed.get("event") if isinstance(feed.get("event"), dict) else {}
    date = feed_event.get("date") if isinstance(feed_event.get("date"), dict) else snapshot.get("date") or {}
    name = str(player.get("name") or "스타플레이어")
    team = str(player.get("team") or "소속팀 미확인")
    date_text = ".".join(str(date.get(key) or "—") for key in ("year", "month", "day"))

    lines = [
        f"# {name} · {date_text} 세계선 기록",
        "",
        "검증된 세이브와 이미 확정된 내장 반응을 장문 기록으로 재구성했다. 모델 창작 결과와는 구분되는 내장 엔진 기록이다.",
        "",
        "## 오늘의 검증 기록",
        "",
        f"- 선수: {name}",
        f"- 팀: {team}",
        f"- 날짜: {date_text}",
    ]
    game_lines = list(feed_event.get("game_lines") or [])
    if not game_lines:
        game_lines = list(event.get("game_lines") or [])
    for value in game_lines:
        if str(value).strip():
            lines.append(f"- 직전 확인 이후 변화분(단일 경기로 단정하지 않음): {str(value).strip()}")
    milestones = list(feed_event.get("milestones") or event.get("milestones") or [])
    for row in milestones:
        value = row[-1] if isinstance(row, (list, tuple)) and row else row
        if str(value or "").strip():
            lines.append(f"- 마일스톤: {str(value).strip()}")
    if not game_lines and not milestones:
        lines.append("- 새로 확정된 경기·마일스톤이 없는 날짜다.")

    articles = list(feed.get("media") or [])
    if articles:
        lines.extend(["", "## 언론 기사", ""])
        for article in articles:
            title = str(article.get("title") or "가상 기사").strip()
            outlet = str(article.get("outlet") or "세계선 언론").strip()
            lines.extend([f"### {outlet} · {title}", ""])
            sub = str(article.get("sub") or "").strip()
            if sub:
                lines.extend([f"**{sub}**", ""])
            for paragraph in article.get("body") or []:
                paragraph = str(paragraph or "").strip()
                if paragraph:
                    lines.extend([paragraph, ""])

    social = list(feed.get("social") or [])
    if social:
        lines.extend(["## 텍스트 SNS", ""])
        for post in social:
            platform = str(post.get("platform") or "텍스트 SNS").strip()
            author = str(post.get("author") or "가상 계정").strip()
            text = str(post.get("text") or "").strip()
            lines.extend([f"### {platform} · {author}", "", text, ""])
            for reply in post.get("replies") or []:
                reply_author = str(reply.get("author") or "익명").strip()
                reply_text = str(reply.get("text") or "").strip()
                if reply_text:
                    lines.append(f"> {reply_author}: {reply_text}")
            lines.append("")

    boards = list(feed.get("boards") or [])
    if boards:
        lines.extend(["## 커뮤니티", ""])
        for board in boards:
            label = str(board.get("board") or "가상 게시판").strip()
            title = str(board.get("title") or "반응 모음").strip()
            lines.extend([f"### {label} · {title}", ""])
            for comment in board.get("comments") or []:
                author = str(comment.get("author") or "익명").strip()
                text = str(comment.get("text") or "").strip()
                if text:
                    lines.append(f"- **{author}**: {text}")
            lines.append("")

    lines.extend(
        [
            "---",
            "",
            "이 문서는 현재 세계선의 검증 기록과 내장 창작 리소스를 재배치한 가상 서사이며 실제 보도가 아니다.",
        ]
    )
    return normalize_generated_markdown("\n".join(lines).strip())


def render_console(feed):
    p, a, ev = feed["player"], feed["assess"], feed["event"]
    print("=" * 66)
    print(f" {p['team']}  {p['name']}  [{a['tone']}]  HEAT {feed['heat']}")
    print(f" 이벤트: {ev['kind']}  " + " · ".join(ev.get("game_lines") or []))
    if ev["milestones"]:
        print(f" 🏆 {ev['milestones'][0][1]}")
    if a.get("line_pit"):
        print(f"   투수  {a['line_pit']}")
    if a.get("line_bat"):
        print(f"   타자  {a['line_bat']}")
    print("=" * 66)
    for art in feed["media"]:
        print(f"\n{art['flag']} 【{art['outlet']}】 {art['title']}")
    for post in feed.get("social") or []:
        print(f"\n〈{post.get('platform', '텍스트 SNS')}〉 {post.get('author', '가상 계정')}: {post.get('text', '')}")
        for reply in post.get("replies") or []:
            print(f"   {reply.get('author', '익명')}: {reply.get('text', '')}")
    for b in feed["boards"]:
        print(f"\n〔{b['board']}〕 {b['title']}")
        for c in b["comments"]:
            up = f" ▲{c['up']}" if "up" in c else ""
            print(f"   {c['author']}: {c['text']}{up}")
