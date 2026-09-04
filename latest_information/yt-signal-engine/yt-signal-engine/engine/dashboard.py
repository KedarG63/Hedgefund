"""Dashboard renderer.

Emits one self-contained HTML file: no build step, no CDN, no fetch. Two modes:

    render(rows, cfg)                  -> full document, open it from disk
    render(rows, cfg, fragment=True)   -> body-only, for publishing as an
                                          Artifact (the host supplies the
                                          <html>/<head>/<body> skeleton)

Design: a wire-service desk. Serif headlines (Newsreader) over a workmanlike
sans (Public Sans), with IBM Plex Mono carrying every number, ticker and
timestamp. Amber is the single accent; green/red/violet/blue are reserved for
stance and signal type and are never used decoratively.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any

TYPE_META = {
    "investable_idea": ("Idea", "idea"),
    "macro_structure": ("Macro", "macro"),
    "research_thread": ("Dig", "dig"),
    "historical_precedent": ("Precedent", "precedent"),
}

STANCE_META = {
    "bullish": "Bull",
    "bearish": "Bear",
    "neutral": "Flat",
    "contested": "Split",
}


def _esc(v: Any) -> str:
    return html.escape(str(v or ""), quote=True)


# Money and ratio figures worth pulling out visually -- $45bn, 20%, 4x. Deliberately
# simple: a miss just means no stat tile for that card, never a broken page.
_NUM_RE = re.compile(
    r"-?\$\s?\d[\d,]*(?:\.\d+)?\s?(?:trillion|billion|million|thousand|bn|mn|tn|[bBmMkK])?"
    r"|-?\d[\d,]*(?:\.\d+)?\s?(?:%|x\b|percent)",
    re.IGNORECASE,
)

# Split after . ! ? only when followed by a capital letter -- keeps "$45.5 billion"
# and "U.S." intact while still separating real sentences.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _lead_stat(text: str) -> str | None:
    """The first money/ratio figure in text, for a headline stat tile."""
    m = _NUM_RE.search(text or "")
    return m.group(0).strip() if m else None


def _highlight_nums(escaped_text: str) -> str:
    """Bold money/ratio figures in already-HTML-escaped text so they scan at a glance."""
    return _NUM_RE.sub(lambda m: f'<strong class="num">{m.group(0)}</strong>', escaped_text)


def _first_sentence(text: str) -> tuple[str, str]:
    """Split off the first sentence; the rest collapses behind a <details> toggle."""
    text = (text or "").strip()
    if not text:
        return "", ""
    parts = _SENTENCE_RE.split(text, maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "")


def _stamp(seconds: Any) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "--:--"
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _date(iso: str) -> str:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).strftime("%d %b %Y")
    except ValueError:
        return (iso or "")[:10]


def _ago(iso: str) -> str:
    try:
        then = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    days = (datetime.now(timezone.utc) - then).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days}d ago"
    return f"{days // 30}mo ago"


CSS = """
:root{
  --ground:#F1F2F4; --surface:#FFFFFF; --surface-2:#F7F8F9; --line:#DEE1E6;
  --ink:#14181F; --ink-2:#3D4653; --ink-3:#6C7686;
  --accent:#B26A16; --accent-soft:#F5E7D4;
  --bull:#1F6F52; --bear:#B03A31; --split:#6B4E9E; --flat:#5A6472;
  --idea:#B26A16; --macro:#2F5F8F; --dig:#6B4E9E; --precedent:#8C5A3B;
  --shadow:0 1px 2px rgba(20,24,31,.05), 0 8px 24px -16px rgba(20,24,31,.28);
  --serif:"Newsreader",Georgia,"Times New Roman",serif;
  --sans:"Public Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#0D1015; --surface:#161B22; --surface-2:#1C222B; --line:#2A323D;
    --ink:#E8EBEF; --ink-2:#B4BCC7; --ink-3:#828D9C;
    --accent:#E0A050; --accent-soft:#33261366;
    --bull:#4FB58A; --bear:#E0736A; --split:#A98BDB; --flat:#8B95A3;
    --idea:#E0A050; --macro:#6BA3DB; --dig:#A98BDB; --precedent:#C99A6F;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"]{
  --ground:#0D1015; --surface:#161B22; --surface-2:#1C222B; --line:#2A323D;
  --ink:#E8EBEF; --ink-2:#B4BCC7; --ink-3:#828D9C;
  --accent:#E0A050; --accent-soft:#33261366;
  --bull:#4FB58A; --bear:#E0736A; --split:#A98BDB; --flat:#8B95A3;
  --idea:#E0A050; --macro:#6BA3DB; --dig:#A98BDB; --precedent:#C99A6F;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.7);
}

*{box-sizing:border-box}
body{
  margin:0; background:var(--ground); color:var(--ink);
  font-family:var(--sans); font-size:15px; line-height:1.55;
  -webkit-font-smoothing:antialiased;
}
a{color:inherit}
.wrap{max-width:1180px; margin:0 auto; padding:0 20px 96px}

/* ---- masthead ---- */
.masthead{
  border-bottom:2px solid var(--ink); margin-bottom:22px; padding:34px 0 14px;
  display:flex; align-items:flex-end; justify-content:space-between; gap:24px; flex-wrap:wrap;
}
.brand h1{
  font-family:var(--serif); font-weight:600; font-size:clamp(30px,4.4vw,46px);
  letter-spacing:-.02em; margin:0; line-height:1.05; text-wrap:balance;
}
.brand p{margin:6px 0 0; color:var(--ink-3); font-size:14px; max-width:56ch}
.deskline{
  font-family:var(--mono); font-size:11px; letter-spacing:.09em; text-transform:uppercase;
  color:var(--ink-3); display:flex; gap:16px; flex-wrap:wrap; align-items:center;
}
.deskline b{color:var(--ink); font-weight:600; font-variant-numeric:tabular-nums}

/* ---- controls ---- */
.controls{
  position:sticky; top:0; z-index:20; background:var(--ground);
  padding:10px 0 12px; border-bottom:1px solid var(--line); margin-bottom:26px;
  display:flex; gap:10px; flex-wrap:wrap; align-items:center;
}
.chipset{display:flex; gap:6px; flex-wrap:wrap}
.chip{
  font-family:var(--mono); font-size:11px; letter-spacing:.06em; text-transform:uppercase;
  padding:6px 11px; border:1px solid var(--line); border-radius:2px; background:var(--surface);
  color:var(--ink-2); cursor:pointer; transition:background .12s,border-color .12s,color .12s;
}
.chip:hover{border-color:var(--ink-3)}
.chip[aria-pressed="true"]{background:var(--ink); border-color:var(--ink); color:var(--ground)}
.chip:focus-visible,.tstamp:focus-visible,.srch:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
.srch{
  flex:1; min-width:180px; font-family:var(--mono); font-size:12px; padding:7px 11px;
  border:1px solid var(--line); border-radius:2px; background:var(--surface); color:var(--ink);
}
.srch::placeholder{color:var(--ink-3)}

/* Active-filter notice. Amber because it is the one accent here, and because a
   restored filter hiding most of the board is the single thing most likely to
   be mistaken for a pipeline that stopped running. */
.filterbar{
  display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin:0 0 22px;
  padding:9px 13px; background:var(--accent-soft); border:1px solid var(--accent);
  border-radius:2px; font-family:var(--mono); font-size:11.5px; letter-spacing:.04em;
  color:var(--ink);
}
.filterbar button{
  font-family:var(--mono); font-size:10.5px; letter-spacing:.07em; text-transform:uppercase;
  margin-left:auto; padding:5px 10px; border:1px solid var(--accent); border-radius:2px;
  background:var(--accent); color:var(--ground); cursor:pointer;
}
.filterbar button:hover{filter:brightness(1.08)}
.filterbar button:focus-visible{outline:2px solid var(--ink); outline-offset:2px}

/* ---- episode card ---- */
.ep{
  background:var(--surface); border:1px solid var(--line); border-radius:3px;
  box-shadow:var(--shadow); margin-bottom:20px; overflow:hidden;
}
.ep-head{display:grid; grid-template-columns:168px minmax(0,1fr); gap:20px; padding:20px}
.ep-cols{display:grid; grid-template-columns:minmax(0,1.05fr) minmax(0,.95fr); gap:24px; align-items:start}
.ep-thumb{
  width:168px; aspect-ratio:16/10; object-fit:cover; border-radius:2px;
  border:1px solid var(--line); background:var(--surface-2); display:block;
}
.ep-meta{
  font-family:var(--mono); font-size:11px; letter-spacing:.07em; text-transform:uppercase;
  color:var(--ink-3); display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin-bottom:9px;
}
.src{color:var(--ink); font-weight:500; border-left:3px solid var(--accent); padding-left:7px}
.ep-head h2{
  font-family:var(--serif); font-size:25px; line-height:1.2; letter-spacing:-.015em;
  font-weight:600; margin:0 0 8px; text-wrap:balance;
}
.orig{
  font-size:12px; color:var(--ink-3); margin:0 0 12px; font-style:italic;
}
.orig a{text-decoration:none; border-bottom:1px solid var(--line)}
.orig a:hover{border-color:var(--accent); color:var(--accent)}
.lede{margin:0 0 14px; color:var(--ink-2)}
.sowhat{
  background:var(--accent-soft); border-left:3px solid var(--accent);
  padding:10px 13px; font-size:14px; color:var(--ink);
}
.sowhat b{
  font-family:var(--mono); font-size:10px; letter-spacing:.12em; text-transform:uppercase;
  color:var(--accent); display:block; margin-bottom:3px;
}
.bullets{margin:0; padding:0; list-style:none; display:flex; flex-direction:column; gap:9px}
.bullets li{
  position:relative; padding-left:17px; font-size:14px; color:var(--ink-2);
}
.bullets-head{
  font-family:var(--mono); font-size:10px; letter-spacing:.12em; text-transform:uppercase;
  color:var(--ink-3); margin:0 0 9px; padding-bottom:6px; border-bottom:1px solid var(--line);
}
.bullets li::before{
  content:""; position:absolute; left:0; top:.62em; width:7px; height:1px; background:var(--ink-3);
}

/* ---- signals ---- */
.sig-head{
  display:flex; align-items:center; gap:10px; padding:13px 20px 0;
  font-family:var(--mono); font-size:10px; letter-spacing:.12em; text-transform:uppercase; color:var(--ink-3);
}
.sig-head::after{content:""; flex:1; height:1px; background:var(--line)}
.sigs{list-style:none; margin:0; padding:8px 20px 20px; display:flex; flex-direction:column}
.sig{
  display:grid; grid-template-columns:64px minmax(0,1.1fr) minmax(0,.9fr); gap:16px;
  padding:15px 0; border-top:1px solid var(--line); align-items:start;
}
.sigs .sig:first-child{border-top:none}
.rail{display:flex; flex-direction:column; gap:6px; align-items:flex-start}
.tag{
  font-family:var(--mono); font-size:9.5px; letter-spacing:.1em; text-transform:uppercase;
  padding:3px 6px; border-radius:2px; color:#fff; white-space:nowrap;
}
.tag.idea{background:var(--idea)} .tag.macro{background:var(--macro)} .tag.dig{background:var(--dig)}
.tag.precedent{background:var(--precedent)}
.era{
  font-family:var(--mono); font-size:9.5px; letter-spacing:.06em; color:var(--precedent);
  border:1px solid currentColor; border-radius:2px; padding:2px 5px; white-space:nowrap;
}
.stance{
  font-family:var(--mono); font-size:9.5px; letter-spacing:.1em; text-transform:uppercase;
  padding:2px 5px; border-radius:2px; border:1px solid currentColor; white-space:nowrap;
}
.s-bullish{color:var(--bull)} .s-bearish{color:var(--bear)}
.s-contested{color:var(--split)} .s-neutral{color:var(--flat)}
.stat{
  font-family:var(--sans); font-weight:600; font-size:23px; line-height:1.1;
  color:var(--ink); margin:0 0 3px; letter-spacing:-.01em;
}
.sig h3{font-size:15.5px; font-weight:600; margin:0 0 5px; line-height:1.35; text-wrap:balance}
.sig p{margin:0 0 9px; color:var(--ink-2); font-size:14px}
.num{font-weight:600; color:var(--ink)}
details.more{margin:0 0 9px}
details.more > summary{
  font-family:var(--mono); font-size:10.5px; letter-spacing:.07em; text-transform:uppercase;
  color:var(--ink-3); cursor:pointer; user-select:none; list-style:revert;
}
details.more > summary:hover{color:var(--accent)}
details.more[open] > summary{margin-bottom:6px}
details.more p.rest{margin:0}
.sig-side{display:flex; flex-direction:column; gap:9px}
.quote{
  border-left:2px solid var(--accent); padding:1px 0 1px 12px; margin:0;
  font-family:var(--serif); font-size:15px; font-style:italic; color:var(--ink-2);
}
.rq{
  background:var(--surface-2); border:1px dashed var(--line); border-radius:2px;
  padding:9px 12px; font-size:13.5px; color:var(--ink-2);
}
.rq b{
  font-family:var(--mono); font-size:9.5px; letter-spacing:.11em; text-transform:uppercase;
  color:var(--dig); display:block; margin-bottom:3px;
}
.foot{display:flex; gap:9px; flex-wrap:wrap; align-items:center}
.tstamp{
  font-family:var(--mono); font-size:11px; font-variant-numeric:tabular-nums;
  text-decoration:none; color:var(--ink); background:var(--surface-2);
  border:1px solid var(--line); border-radius:2px; padding:3px 8px; transition:.12s;
}
.tstamp:hover{border-color:var(--accent); color:var(--accent)}
.ent{
  font-family:var(--mono); font-size:10.5px; letter-spacing:.04em; color:var(--ink-3);
  border:1px solid var(--line); border-radius:2px; padding:3px 7px;
}
.conf{font-family:var(--mono); font-size:10px; color:var(--ink-3); font-variant-numeric:tabular-nums}

.empty{
  text-align:center; padding:60px 20px; color:var(--ink-3);
  font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase;
}
.hidden{display:none !important}
footer{
  margin-top:34px; padding-top:16px; border-top:1px solid var(--line);
  font-family:var(--mono); font-size:11px; color:var(--ink-3); line-height:1.7;
}
@media (max-width:940px){
  .ep-cols{grid-template-columns:1fr; gap:16px}
  .sig{grid-template-columns:64px minmax(0,1fr)}
  .sig-side{grid-column:2}
}
@media (max-width:720px){
  .ep-head{grid-template-columns:1fr}
  .ep-thumb{width:100%; max-width:280px}
  .sig{grid-template-columns:1fr; gap:9px}
  .sig-side{grid-column:1}
  .rail{flex-direction:row}
}
@media (prefers-reduced-motion:reduce){*{transition:none !important; animation:none !important}}
"""

JS = """
(function(){
  var state={src:"all",type:"all",q:""};
  try{var s=localStorage.getItem("sigdesk");if(s){state=Object.assign(state,JSON.parse(s));}}catch(e){}

  function save(){try{localStorage.setItem("sigdesk",JSON.stringify(state));}catch(e){}}

  function apply(){
    // All three counters describe the SAME filtered set. Leaving the episode
    // count reactive while the signal counts stayed at the build-time total
    // read as a stale dashboard: filter to one source and you get that source's
    // episode count sitting next to every signal in the file.
    var eps=document.querySelectorAll(".ep"),shown=0,nSigs=0,nDig=0;
    var q=state.q.trim().toLowerCase();
    for(var i=0;i<eps.length;i++){
      var ep=eps[i];
      var okSrc=state.src==="all"||ep.dataset.src===state.src;
      var sigs=ep.querySelectorAll(".sig"),vis=0,dig=0;
      for(var j=0;j<sigs.length;j++){
        var sg=sigs[j];
        var okType=state.type==="all"||sg.dataset.type===state.type;
        var okQ=!q||sg.dataset.hay.indexOf(q)>-1;
        var on=okType&&okQ;
        sg.classList.toggle("hidden",!on);
        if(on){vis++;if(sg.dataset.type==="research_thread")dig++;}
      }
      var epQ=!q||ep.dataset.hay.indexOf(q)>-1;
      var on=okSrc&&(vis>0||(epQ&&state.type==="all"));
      ep.classList.toggle("hidden",!on);
      if(on){shown++;nSigs+=vis;nDig+=dig;}
    }
    var e=document.getElementById("empty");
    if(e)e.classList.toggle("hidden",shown>0);
    var c=document.getElementById("shown");
    if(c)c.textContent=shown;
    var cs=document.getElementById("n-sigs");
    if(cs)cs.textContent=nSigs;
    var cd=document.getElementById("n-dig");
    if(cd)cd.textContent=nDig;

    // The filter is restored from localStorage on load, so a source picked days
    // ago silently hides most of the board and the page looks like a pipeline
    // that stopped updating. Say so in words, next to the way out.
    var bar=document.getElementById("filterbar"),txt=document.getElementById("filtertext");
    if(bar&&txt){
      var bits=[];
      if(state.src!=="all"){
        var chip=document.querySelector('[data-group="src"][data-val="'+state.src+'"]');
        bits.push("source "+(chip?chip.textContent.trim():state.src));
      }
      if(state.type!=="all"){
        var tc=document.querySelector('[data-group="type"][data-val="'+state.type+'"]');
        bits.push("type "+(tc?tc.textContent.trim():state.type));
      }
      if(q)bits.push('search "'+state.q.trim()+'"');
      var on=bits.length>0;
      bar.classList.toggle("hidden",!on);
      if(on){
        txt.textContent="Filtered by "+bits.join(" + ")+" \\u2014 showing "+shown+
                        " of "+eps.length+" episodes";
      }
    }
  }

  function reset(){
    state.src="all";state.type="all";state.q="";
    var box=document.getElementById("q");if(box)box.value="";
    var all=document.querySelectorAll('[data-group]');
    for(var i=0;i<all.length;i++){
      all[i].setAttribute("aria-pressed",String(all[i].dataset.val==="all"));
    }
    save();apply();
  }

  function bind(group,key){
    var btns=document.querySelectorAll('[data-group="'+group+'"]');
    for(var i=0;i<btns.length;i++){
      (function(b){
        b.setAttribute("aria-pressed",String(b.dataset.val===state[key]));
        b.addEventListener("click",function(){
          state[key]=b.dataset.val;
          for(var k=0;k<btns.length;k++)btns[k].setAttribute("aria-pressed",String(btns[k]===b));
          save();apply();
        });
      })(btns[i]);
    }
  }
  bind("src","src");bind("type","type");

  var box=document.getElementById("q");
  if(box){
    box.value=state.q;
    box.addEventListener("input",function(){state.q=box.value;save();apply();});
  }
  var clr=document.getElementById("clearfilters");
  if(clr)clr.addEventListener("click",reset);
  apply();
})();
"""


def _signal_html(item: dict, video_url: str) -> str:
    label, cls = TYPE_META.get(item.get("type", ""), ("Note", "macro"))
    stance = (item.get("stance") or "neutral").lower()
    conf = item.get("confidence")
    t = item.get("t_start")
    ents = item.get("entities") or []

    hay = " ".join(
        str(x).lower()
        for x in [item.get("headline"), item.get("body"), item.get("quote"),
                  item.get("research_question"), item.get("era"),
                  " ".join(map(str, ents)), label, stance]
    )

    parts = [
        f'<li class="sig" data-type="{_esc(item.get("type"))}" data-hay="{_esc(hay)}">',
        '<div class="rail">',
        f'<span class="tag {cls}">{_esc(label)}</span>',
        f'<span class="stance s-{_esc(stance)}">{_esc(STANCE_META.get(stance, "Flat"))}</span>',
    ]
    if item.get("era"):
        parts.append(f'<span class="era">{_esc(item["era"])}</span>')
    parts.append('</div><div class="sig-main">')
    stat = _lead_stat(item.get("headline") or "")
    if stat:
        parts.append(f'<div class="stat">{_esc(stat)}</div>')
    parts.append(f'<h3>{_esc(item.get("headline"))}</h3>')
    if item.get("body"):
        first, rest = _first_sentence(item["body"])
        parts.append(f'<p>{_highlight_nums(_esc(first))}</p>')
        if rest:
            parts.append(
                f'<details class="more"><summary>more</summary>'
                f'<p class="rest">{_highlight_nums(_esc(rest))}</p></details>'
            )

    parts.append('<div class="foot">')
    if t is not None:
        parts.append(
            f'<a class="tstamp" href="{_esc(video_url)}&amp;t={int(t)}s" target="_blank" '
            f'rel="noopener">▶ {_stamp(t)}</a>'
        )
    for e in ents[:6]:
        parts.append(f'<span class="ent">{_esc(e)}</span>')
    if conf is not None:
        try:
            parts.append(f'<span class="conf">conf {float(conf):.2f}</span>')
        except (TypeError, ValueError):
            pass
    parts.append('</div></div><div class="sig-side">')

    if item.get("quote"):
        parts.append(f'<blockquote class="quote">“{_highlight_nums(_esc(item["quote"]))}”</blockquote>')
    if item.get("research_question"):
        parts.append(
            f'<div class="rq"><b>Go find out</b>{_esc(item["research_question"])}</div>'
        )

    parts.append("</div></li>")
    return "".join(parts)


def _episode_html(row: dict) -> str:
    url = row.get("url") or f"https://www.youtube.com/watch?v={row.get('video_id')}"
    items = row.get("items") or []
    hay = " ".join(
        str(x).lower()
        for x in [row.get("s_headline"), row.get("title"), row.get("s_summary"),
                  row.get("so_what"), row.get("channel_name"), " ".join(map(str, row.get("bullets") or []))]
    )

    all_bullets = row.get("bullets") or []
    bullets = "".join(f"<li>{_highlight_nums(_esc(b))}</li>" for b in all_bullets[:3])
    bullets_extra = ""
    if len(all_bullets) > 3:
        extra_items = "".join(f"<li>{_highlight_nums(_esc(b))}</li>" for b in all_bullets[3:])
        bullets_extra = (
            f'<details class="more"><summary>+{len(all_bullets) - 3} more</summary>'
            f'<ul class="bullets rest">{extra_items}</ul></details>'
        )

    lede_first, lede_rest = _first_sentence(row.get("s_summary") or "")
    lede_html = f'<p class="lede">{_highlight_nums(_esc(lede_first))}</p>'
    if lede_rest:
        lede_html += (
            f'<details class="more"><summary>more</summary>'
            f'<p class="lede rest">{_highlight_nums(_esc(lede_rest))}</p></details>'
        )

    sigs = "".join(_signal_html(i, url) for i in items)

    counts: dict[str, int] = {}
    for i in items:
        counts[i.get("type", "")] = counts.get(i.get("type", ""), 0) + 1
    tally = " · ".join(
        f"{n} {TYPE_META.get(t, ('note', ''))[0].lower()}{'s' if n > 1 else ''}"
        for t, n in counts.items() if t
    ) or "no signals"

    return f"""
<article class="ep" data-src="{_esc(row.get('channel_id'))}" data-hay="{_esc(hay)}">
  <div class="ep-head">
    <img class="ep-thumb" src="{_esc(row.get('thumbnail'))}" alt="" loading="lazy">
    <div>
      <div class="ep-meta">
        <span class="src">{_esc(row.get('channel_name'))}</span>
        <span>{_esc(_date(row.get('published', '')))}</span>
        <span>{_esc(_ago(row.get('published', '')))}</span>
        {f"<span>{_stamp(row.get('duration_sec'))}</span>" if row.get('duration_sec') else ""}
        <span>{tally}</span>
      </div>
      <h2>{_esc(row.get('s_headline') or row.get('title'))}</h2>
      <p class="orig">Published as &ldquo;{_esc(row.get('title'))}&rdquo; &mdash;
        <a href="{_esc(url)}" target="_blank" rel="noopener">watch</a></p>
      <div class="ep-cols">
        <div>
          {lede_html}
          <div class="sowhat"><b>So what</b>{_highlight_nums(_esc(row.get('so_what')))}</div>
        </div>
        <div>
          <p class="bullets-head">Takeaways</p>
          <ul class="bullets">{bullets}</ul>
          {bullets_extra}
        </div>
      </div>
    </div>
  </div>
  <div class="sig-head">Signals</div>
  <ul class="sigs">{sigs}</ul>
</article>"""


def render(rows: list[dict], cfg, fragment: bool = False, sources: list | None = None) -> str:
    n_items = sum(len(r.get("items") or []) for r in rows)
    n_dig = sum(1 for r in rows for i in (r.get("items") or []) if i.get("type") == "research_thread")
    generated = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    srcs = sources or []
    src_chips = "".join(
        f'<button class="chip" data-group="src" data-val="{_esc(c.channel_id)}">{_esc(c.name)}</button>'
        for c in srcs
    )
    type_chips = "".join(
        f'<button class="chip" data-group="type" data-val="{k}">{_esc(v[0])}</button>'
        for k, v in TYPE_META.items()
    )

    body = f"""
<div class="wrap">
  <header class="masthead">
    <div class="brand">
      <h1>{_esc(cfg.title)}</h1>
      <p>{_esc(cfg.subtitle)}</p>
    </div>
    <div class="deskline">
      <span><b id="shown">{len(rows)}</b> episodes</span>
      <span><b id="n-sigs">{n_items}</b> signals</span>
      <span><b id="n-dig">{n_dig}</b> to dig into</span>
      <span>built {generated}</span>
    </div>
  </header>

  <div class="controls">
    <div class="chipset">
      <button class="chip" data-group="src" data-val="all">All sources</button>
      {src_chips}
    </div>
    <div class="chipset">
      <button class="chip" data-group="type" data-val="all">All signals</button>
      {type_chips}
    </div>
    <input class="srch" id="q" type="search" placeholder="filter by ticker, company, claim…"
           aria-label="Filter signals">
  </div>

  <div class="filterbar hidden" id="filterbar" role="status">
    <span id="filtertext"></span>
    <button type="button" id="clearfilters">Show everything</button>
  </div>

  {"".join(_episode_html(r) for r in rows)}

  <div class="empty hidden" id="empty">Nothing matches those filters</div>

  <footer>
    Generated by yt-signal-engine from public YouTube caption tracks.<br>
    Every claim here is what a podcast host said, not a fact and not advice.
    Timestamps link to the exact second so you can check it yourself.
  </footer>
</div>
<script>{JS}</script>"""

    fonts = (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        "family=Newsreader:ital,opsz,wght@0,6..72,400..700;1,6..72,400&"
        "family=Public+Sans:wght@400;500;600&"
        'family=IBM+Plex+Mono:wght@400;500&display=swap">'
    )

    if fragment:
        return f"<title>{_esc(cfg.title)}</title>\n{fonts}\n<style>{CSS}</style>\n{body}"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(cfg.title)}</title>
{fonts}
<style>{CSS}</style>
</head>
<body>
{body}
</body>
</html>"""


def render_json(rows: list[dict]) -> str:
    """Same data as the dashboard, for feeding another tool."""
    return json.dumps(rows, indent=2, default=str)
