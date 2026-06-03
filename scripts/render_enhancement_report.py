#!/usr/bin/env python3
"""Render the crm-agentic-enhancements workflow output into a self-contained HTML report.

Usage:
    python scripts/render_enhancement_report.py <report.json> <out.html>

The JSON is the object returned by the `crm-agentic-enhancements` workflow:
its `synthesis` key holds the report body (executiveSummary, currentState,
*Mermaid diagrams, recommendations, impactEffortMatrix, openQuestions, sources).
Optional top-level keys (verifiedCount, refutedCount, refuted, ground) are
surfaced as an appendix.

Output is one HTML file: embedded CSS, sticky sidebar nav with scroll-spy, and
Mermaid (loaded from CDN) for the diagrams. No build step, no server.
"""
from __future__ import annotations

import html
import json
import re
import sys
from datetime import date
from pathlib import Path

IMPACT_ORDER = {"High": 0, "Medium": 1, "Low": 2}
EFFORT_ORDER = {"S": 0, "M": 1, "L": 2}
EFFORT_LABEL = {"S": "Small", "M": "Medium", "L": "Large"}


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "section"


def chip(kind: str, label: str, value: str) -> str:
    return (
        f'<span class="chip chip--{kind} chip--{slug(value)}">'
        f'<span class="chip__k">{esc(label)}</span>{esc(value)}</span>'
    )


def mermaid_block(source) -> str:
    if not source or not str(source).strip():
        return ""
    return f'<pre class="mermaid">\n{esc(source)}\n</pre>'


def bullet_list(items) -> str:
    items = [i for i in (items or []) if str(i).strip()]
    if not items:
        return ""
    lis = "".join(f"<li>{esc(i)}</li>" for i in items)
    return f"<ul>{lis}</ul>"


def pointer_list(label: str, items) -> str:
    items = [i for i in (items or []) if str(i).strip()]
    if not items:
        return ""
    codes = "".join(f"<code>{esc(i)}</code>" for i in items)
    return f'<div class="ptrs"><span class="ptrs__l">{esc(label)}</span>{codes}</div>'


def rec_sort_key(rec: dict):
    return (
        rec.get("priority", 999),
        IMPACT_ORDER.get(rec.get("impact", "Low"), 9),
        EFFORT_ORDER.get(rec.get("effort", "L"), 9),
    )


def render_recommendation(rec: dict) -> str:
    rid = rec.get("id", "")
    parts = [f'<article class="rec" id="rec-{esc(slug(rid))}">']
    parts.append('<div class="rec__head">')
    parts.append(f'<span class="rec__pri">#{esc(rec.get("priority", "-"))}</span>')
    parts.append(f'<h3 class="rec__title">{esc(rec.get("title", rid))}</h3>')
    parts.append("</div>")
    parts.append('<div class="rec__badges">')
    parts.append(chip("cat", "", rec.get("category", "")))
    parts.append(chip("impact", "impact", rec.get("impact", "")))
    parts.append(chip("effort", "effort", EFFORT_LABEL.get(rec.get("effort", ""), rec.get("effort", ""))))
    parts.append(f'<span class="rec__id">{esc(rid)}</span>')
    parts.append("</div>")
    if rec.get("problem"):
        parts.append(f'<div class="field"><span class="field__l">Problem</span><p>{esc(rec["problem"])}</p></div>')
    if rec.get("proposal"):
        parts.append(f'<div class="field"><span class="field__l">Proposal</span><p>{esc(rec["proposal"])}</p></div>')
    if rec.get("rationale"):
        parts.append(f'<div class="field"><span class="field__l">Why it matters</span><p>{esc(rec["rationale"])}</p></div>')
    parts.append(mermaid_block(rec.get("mermaid")))
    parts.append(pointer_list("code", rec.get("codePointers")))
    parts.append(pointer_list("refs", rec.get("citations")))
    parts.append("</article>")
    return "".join(parts)


def render_matrix(matrix: list) -> str:
    """3x3 grid: impact (rows, High->Low) x effort (cols, S->L). Quick wins = High/S."""
    cells: dict[tuple[str, str], list] = {}
    for m in matrix or []:
        cells.setdefault((m.get("impact", "Low"), m.get("effort", "L")), []).append(m)
    rows = []
    rows.append('<div class="matrix">')
    rows.append('<div class="matrix__corner">impact &darr; / effort &rarr;</div>')
    for eff in ("S", "M", "L"):
        rows.append(f'<div class="matrix__colh">{esc(EFFORT_LABEL[eff])} effort</div>')
    for imp in ("High", "Medium", "Low"):
        rows.append(f'<div class="matrix__rowh">{esc(imp)} impact</div>')
        for eff in ("S", "M", "L"):
            quick = imp == "High" and eff == "S"
            klass = "matrix__cell" + (" matrix__cell--quick" if quick else "")
            chips = "".join(
                f'<a class="mx-chip mx-chip--{slug(imp)}" href="#rec-{esc(slug(m.get("id","")))}" '
                f'title="{esc(m.get("title",""))}">{esc(m.get("id",""))}</a>'
                for m in cells.get((imp, eff), [])
            )
            tag = '<span class="matrix__qtag">quick wins</span>' if quick else ""
            rows.append(f'<div class="{klass}">{tag}{chips}</div>')
    rows.append("</div>")
    return "".join(rows)


def render(data: dict) -> str:
    syn = data.get("synthesis") or data
    recs = sorted(syn.get("recommendations", []), key=rec_sort_key)

    # group by category, ordered by best (lowest) priority in each group
    cats: dict[str, list] = {}
    for r in recs:
        cats.setdefault(r.get("category", "Uncategorised"), []).append(r)
    ordered_cats = sorted(cats.items(), key=lambda kv: min(rec_sort_key(r) for r in kv[1]))

    cur = syn.get("currentState", {}) or {}
    verified = data.get("verifiedCount", len(recs))
    refuted = data.get("refutedCount", 0)
    refuted_list = data.get("refuted", []) or []

    nav = [
        ("overview", "Overview"),
        ("current-state", "Current state"),
        ("arch-today", "Architecture today"),
        ("target", "Target pipeline"),
        ("roadmap", "Roadmap"),
        ("matrix", "Impact / effort"),
        ("recommendations", "Recommendations"),
    ]
    nav += [(f"cat-{slug(c)}", "&rsaquo; " + c) for c, _ in ordered_cats]
    nav += [("open-questions", "Open questions"), ("sources", "Sources")]
    if refuted_list:
        nav.append(("refuted", "Considered &amp; dropped"))

    nav_html = "".join(
        f'<a class="nav__link" href="#{nid}">{label}</a>' for nid, label in nav
    )

    # ---- sections ----
    S = []
    S.append(
        f'<section id="overview" class="sec"><h2>Overview</h2>'
        f'<p class="lede">{esc(syn.get("executiveSummary", ""))}</p>'
        f'<div class="stats">'
        f'<div class="stat"><b>{esc(verified)}</b><span>verified enhancements</span></div>'
        f'<div class="stat"><b>{esc(len(ordered_cats))}</b><span>focus areas</span></div>'
        f'<div class="stat"><b>{esc(refuted)}</b><span>findings refuted / dropped</span></div>'
        f'</div></section>'
    )

    S.append(
        f'<section id="current-state" class="sec"><h2>Current state</h2>'
        f'<p>{esc(cur.get("summary", ""))}</p>'
        f'<div class="two-col">'
        f'<div class="panel panel--good"><h3>Strengths</h3>{bullet_list(cur.get("strengths"))}</div>'
        f'<div class="panel panel--gap"><h3>Gaps</h3>{bullet_list(cur.get("gaps"))}</div>'
        f'</div></section>'
    )

    S.append(
        f'<section id="arch-today" class="sec"><h2>Architecture today</h2>'
        f'<div class="diagram">{mermaid_block(syn.get("architectureMermaid"))}</div></section>'
    )
    S.append(
        f'<section id="target" class="sec"><h2>Target agentic-customization pipeline</h2>'
        f'<div class="diagram">{mermaid_block(syn.get("targetMermaid"))}</div></section>'
    )
    S.append(
        f'<section id="roadmap" class="sec"><h2>Roadmap</h2>'
        f'<div class="diagram">{mermaid_block(syn.get("roadmapMermaid"))}</div></section>'
    )
    S.append(
        f'<section id="matrix" class="sec"><h2>Impact / effort</h2>'
        f'<p class="hint">Top-left = highest leverage. Tap a chip to jump to the recommendation.</p>'
        f'{render_matrix(syn.get("impactEffortMatrix"))}</section>'
    )

    rec_sections = [
        f'<section id="cat-{slug(cat)}" class="sec sec--cat"><h2>{esc(cat)}</h2>'
        + "".join(render_recommendation(r) for r in sorted(group, key=rec_sort_key))
        + "</section>"
        for cat, group in ordered_cats
    ]
    S.append(
        '<section id="recommendations" class="sec sec--anchor"><h2>Recommendations</h2>'
        '<p class="hint">Ranked by priority, grouped by focus area.</p></section>'
        + "".join(rec_sections)
    )

    S.append(
        f'<section id="open-questions" class="sec"><h2>Open questions</h2>'
        f'{bullet_list(syn.get("openQuestions"))}</section>'
    )

    src_items = syn.get("sources", []) or []
    src_html = "".join(
        (f'<li><a href="{esc(s)}" target="_blank" rel="noopener">{esc(s)}</a></li>'
         if str(s).startswith("http") else f"<li>{esc(s)}</li>")
        for s in src_items
    )
    S.append(f'<section id="sources" class="sec"><h2>Sources</h2><ul class="sources">{src_html}</ul></section>')

    if refuted_list:
        rrows = "".join(
            f'<tr><td>{esc(r.get("title",""))}</td><td>{esc(r.get("category",""))}</td>'
            f'<td>{esc(r.get("note",""))}</td></tr>'
            for r in refuted_list
        )
        S.append(
            '<section id="refuted" class="sec"><h2>Considered &amp; dropped</h2>'
            '<p class="hint">Candidate ideas the verification pass rejected as already-implemented or '
            'inaccurate for on-prem. Kept for transparency.</p>'
            '<table class="tbl"><thead><tr><th>Idea</th><th>Area</th><th>Why dropped</th></tr></thead>'
            f'<tbody>{rrows}</tbody></table></section>'
        )

    body = "\n".join(S)
    today = date.today().isoformat()

    return TEMPLATE.replace("__NAV__", nav_html).replace("__BODY__", body).replace("__DATE__", today)


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>crm CLI — agentic & on-prem customization enhancements</title>
<style>
:root{
  --bg:#0f1117; --surface:#171a23; --surface2:#1d212c; --line:#2a2f3d;
  --ink:#e6e9f0; --muted:#9aa3b2; --faint:#6b7385;
  --accent:#6ea8fe; --accent2:#7ee0c0;
  --high:#ff6b6b; --med:#ffc14d; --low:#8b93a7;
  --eff-s:#7ee0c0; --eff-m:#ffc14d; --eff-l:#ff8a6b;
  --good:#7ee0c0; --gap:#ff9d8a;
  --radius:12px; --maxw:980px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
code,pre,.mono{font-family:"SF Mono",ui-monospace,"Cascadia Code",Consolas,monospace}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}

.layout{display:grid;grid-template-columns:264px 1fr;min-height:100vh}
.side{position:sticky;top:0;height:100vh;overflow-y:auto;border-right:1px solid var(--line);
  background:linear-gradient(180deg,#12141c,#0f1117);padding:22px 14px}
.brand{padding:6px 10px 14px}
.brand b{font-size:17px;letter-spacing:.2px}
.brand span{display:block;color:var(--faint);font-size:12px;margin-top:3px}
.nav{display:flex;flex-direction:column;gap:1px;margin-top:8px}
.nav__link{color:var(--muted);padding:7px 10px;border-radius:8px;font-size:13.5px;
  border-left:2px solid transparent;transition:.15s}
.nav__link:hover{color:var(--ink);background:var(--surface);text-decoration:none}
.nav__link.is-active{color:var(--ink);background:var(--surface2);border-left-color:var(--accent)}

.main{min-width:0}
.hero{padding:46px 32px 26px;border-bottom:1px solid var(--line);
  background:radial-gradient(1200px 300px at 0% -10%,rgba(110,168,254,.13),transparent)}
.hero h1{margin:0;font-size:30px;line-height:1.2;letter-spacing:-.4px}
.hero p{color:var(--muted);max-width:720px;margin:12px 0 0}
.hero .tagrow{margin-top:16px;display:flex;gap:8px;flex-wrap:wrap}
.tag{font-size:12px;color:var(--muted);background:var(--surface);border:1px solid var(--line);
  padding:4px 10px;border-radius:20px}
.tag b{color:var(--ink)}

.wrap{max-width:var(--maxw);padding:8px 32px 80px;margin:0}
.sec{padding:30px 0;border-bottom:1px solid var(--line);scroll-margin-top:14px}
.sec:last-child{border-bottom:none}
.sec--anchor{padding-bottom:8px;border-bottom:none}
.sec--cat{padding-top:18px;border-bottom:none}
.sec h2{font-size:21px;margin:0 0 14px;letter-spacing:-.2px}
.sec--cat h2{font-size:17px;color:var(--accent2);border-left:3px solid var(--accent2);padding-left:10px}
.sec h3{font-size:15px;margin:0 0 8px}
.lede{font-size:16.5px;line-height:1.7;color:#d6dae4}
.hint{color:var(--faint);font-size:13px;margin:-4px 0 14px}

.stats{display:flex;gap:14px;flex-wrap:wrap;margin-top:18px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:14px 18px;min-width:140px}
.stat b{font-size:26px;display:block;color:var(--accent)}
.stat span{color:var(--muted);font-size:12.5px}

.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.panel{border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;background:var(--surface)}
.panel--good{border-top:3px solid var(--good)}
.panel--gap{border-top:3px solid var(--gap)}
.panel ul{margin:6px 0 0;padding-left:18px}
.panel li{margin:5px 0}

.diagram{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:18px;overflow-x:auto;text-align:center}
pre.mermaid{margin:0;background:transparent;display:inline-block;min-width:100%}

.rec{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:18px 20px;margin:14px 0;scroll-margin-top:14px;transition:.2s}
.rec:target{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.rec__head{display:flex;align-items:baseline;gap:10px}
.rec__pri{color:var(--accent);font-weight:700;font-size:14px;font-family:ui-monospace,monospace}
.rec__title{margin:0;font-size:16.5px}
.rec__badges{display:flex;gap:7px;flex-wrap:wrap;align-items:center;margin:10px 0 4px}
.rec__id{margin-left:auto;color:var(--faint);font-size:11.5px;font-family:ui-monospace,monospace}
.field{margin-top:12px}
.field__l{display:block;text-transform:uppercase;letter-spacing:.08em;font-size:10.5px;
  color:var(--faint);margin-bottom:2px}
.field p{margin:0;color:#d6dae4}
.rec .diagram{margin-top:12px;text-align:left}

.chip{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;
  padding:3px 9px;border-radius:20px;border:1px solid var(--line);background:var(--surface2);color:var(--ink)}
.chip__k{color:var(--faint);font-weight:500;text-transform:uppercase;font-size:9.5px;letter-spacing:.05em}
.chip--cat{background:rgba(110,168,254,.12);border-color:rgba(110,168,254,.35);color:#bcd4ff}
.chip--high{color:#ffd0d0;border-color:rgba(255,107,107,.5);background:rgba(255,107,107,.12)}
.chip--medium{color:#ffe6b0;border-color:rgba(255,193,77,.5);background:rgba(255,193,77,.12)}
.chip--low{color:#cfd4df;border-color:var(--line)}
.chip--small{color:#bff0df;border-color:rgba(126,224,192,.5);background:rgba(126,224,192,.12)}
.chip--large{color:#ffd2c2;border-color:rgba(255,138,107,.5);background:rgba(255,138,107,.12)}

.ptrs{margin-top:12px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.ptrs__l{text-transform:uppercase;letter-spacing:.08em;font-size:10px;color:var(--faint);margin-right:2px}
.ptrs code{background:var(--surface2);border:1px solid var(--line);border-radius:6px;
  padding:2px 7px;font-size:12px;color:#c7cedd}

.matrix{display:grid;grid-template-columns:140px repeat(3,1fr);gap:8px;margin-top:8px}
.matrix__corner{font-size:11px;color:var(--faint);display:flex;align-items:flex-end}
.matrix__colh{font-size:12px;color:var(--muted);text-align:center;align-self:end;padding-bottom:4px;font-weight:600}
.matrix__rowh{font-size:12px;color:var(--muted);display:flex;align-items:center;font-weight:600}
.matrix__cell{min-height:64px;background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:8px;display:flex;flex-wrap:wrap;gap:5px;align-content:flex-start;position:relative}
.matrix__cell--quick{border-color:rgba(126,224,192,.6);background:rgba(126,224,192,.07)}
.matrix__qtag{position:absolute;top:6px;right:8px;font-size:9.5px;color:var(--accent2);
  text-transform:uppercase;letter-spacing:.06em}
.mx-chip{font-size:11px;font-family:ui-monospace,monospace;padding:3px 7px;border-radius:6px;
  border:1px solid var(--line);background:var(--surface2);color:var(--ink);height:fit-content}
.mx-chip:hover{text-decoration:none;border-color:var(--accent)}
.mx-chip--high{border-color:rgba(255,107,107,.45)}
.mx-chip--medium{border-color:rgba(255,193,77,.45)}

.sources{padding-left:18px}
.sources li{margin:5px 0;word-break:break-all}
.tbl{width:100%;border-collapse:collapse;font-size:13.5px}
.tbl th,.tbl td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
.tbl th{color:var(--muted);font-weight:600}

.foot{color:var(--faint);font-size:12px;padding:24px 32px;border-top:1px solid var(--line)}

@media (max-width:860px){
  .layout{grid-template-columns:1fr}
  .side{position:static;height:auto;border-right:none;border-bottom:1px solid var(--line)}
  .nav{flex-direction:row;flex-wrap:wrap}
  .two-col{grid-template-columns:1fr}
  .matrix{grid-template-columns:90px repeat(3,1fr)}
}
</style>
</head>
<body>
<div class="layout">
  <aside class="side">
    <div class="brand"><b>crm CLI</b><span>agentic + on-prem customization enhancements</span></div>
    <nav class="nav">__NAV__</nav>
  </aside>
  <div class="main">
    <header class="hero">
      <h1>Enhancing the <span class="mono">crm</span> CLI for agentic coding &amp; D365 on-prem customization</h1>
      <p>A repo-grounded, adversarially verified set of enhancements: how to make the
         Dynamics 365 CE on-prem (v9.x, Web API / NTLM) CLI a first-class actuator for AI coding agents
         and for on-prem solution / customization ALM.</p>
      <div class="tagrow">
        <span class="tag">generated <b>__DATE__</b></span>
        <span class="tag">D365 CE <b>on-prem v9.x</b></span>
        <span class="tag"><b>NTLM</b> · Web API OData v4</span>
        <span class="tag">verified vs <b>the repo</b></span>
      </div>
    </header>
    <div class="wrap">__BODY__</div>
    <footer class="foot">
      Generated by the <code>crm-agentic-enhancements</code> workflow — finders &rarr; adversarial
      repo verification &rarr; synthesis. Diagrams rendered with Mermaid.
    </footer>
  </div>
</div>
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
  mermaid.initialize({
    startOnLoad:true, securityLevel:'loose',
    theme:'base',
    themeVariables:{
      background:'#171a23', primaryColor:'#1d212c', primaryTextColor:'#e6e9f0',
      primaryBorderColor:'#3a4256', lineColor:'#6ea8fe', secondaryColor:'#22364a',
      tertiaryColor:'#1d212c', fontSize:'14px'
    }
  });
  // scroll-spy: highlight the nav link of the section in view
  const links = [...document.querySelectorAll('.nav__link')];
  const map = new Map(links.map(l => [l.getAttribute('href').slice(1), l]));
  const obs = new IntersectionObserver((entries)=>{
    entries.forEach(e=>{
      if(e.isIntersecting){
        links.forEach(l=>l.classList.remove('is-active'));
        const a = map.get(e.target.id); if(a) a.classList.add('is-active');
      }
    });
  },{rootMargin:'-10% 0px -75% 0px',threshold:0});
  document.querySelectorAll('section[id]').forEach(s=>obs.observe(s));
</script>
</body>
</html>
"""


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    data = json.loads(src.read_text(encoding="utf-8"))
    out.write_text(render(data), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
